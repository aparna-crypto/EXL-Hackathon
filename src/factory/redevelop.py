"""Stage 4 - Redevelop: rebuild a legacy R model in Python and prove parity.

1. The translation agent writes a Python module with a fixed interface (agents.translation).
2. Static checks: syntax, allowed imports, required interface, no unsafe calls.
3. Unit tests of every transformation: the same synthetic edge cases are run through the R
   functions (r/oracle.R, which loads the legacy script's functions without its data steps) and the
   Python functions (factory.redevelop_harness, in a subprocess); results must match.
4. End-to-end parity on the original split (against the committed R reference outputs) and on a
   resampled split the agent never saw (R re-run): bins, WoE, IV selection, coefficients,
   predictions, scores, AUC/Gini/KS and score distribution.
5. Self-healing: failures go back to the agent; after `max_retries_before_escalation` failed
   attempts the agent's escalation model (Opus) takes over, up to `max_attempts`.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import mlflow
import numpy as np
import pandas as pd

from factory import discover, model_card
from factory.agents import AgentError
from factory.agents import translation as tr

STAGE = "redevelop"
REF_FILES = ["bins.csv", "coefficients.csv", "predictions_test.csv"]

TRANSFORMATIONS = {
    "is_binned_numeric": "Numeric vs categorical rule (>10 distinct values)",
    "numeric_breaks": "Quantile break points (type 7, outer -Inf/Inf)",
    "assign_bin": "Bin assignment and R cut() labels",
    "woe_table": "WoE and IV per bin (0.5 smoothing)",
    "apply_woe": "WoE substitution (unseen level -> 0)",
    "to_score": "Score scaling (600 at 50:1, PDO 20)",
    "auc": "AUC (rank-based, ties)",
    "ks": "KS statistic (ties)",
}

_rng = np.random.default_rng(42)
_GAMMA = np.round(_rng.gamma(2.0, 1500.0, 60), 1).tolist()
_TIED = [0.1, 0.4, 0.35, 0.8, 0.4, 0.4]
_TABLE = [{"bin": "A11", "n_good": 3, "n_bad": 1, "woe": 0.25, "iv_part": 0.01},
          {"bin": "A12", "n_good": 1, "n_bad": 2, "woe": -0.5, "iv_part": 0.02}]

# Synthetic edge cases for the transformation unit tests (no real data).
CASES = [
    ("is_binned_numeric", {"x": list(range(1, 11))}),
    ("is_binned_numeric", {"x": list(range(1, 12))}),
    ("is_binned_numeric", {"x": [f"A{i}" for i in range(12)]}),
    ("is_binned_numeric", {"x": [i + 0.5 for i in range(11)]}),
    ("numeric_breaks", {"x": list(range(1, 21))}),
    ("numeric_breaks", {"x": [1] * 7 + [2, 3, 1248.2, 1000000]}),
    ("numeric_breaks", {"x": _GAMMA}),
    ("assign_bin", {"x": [4, 12, 13, 15, 24, 25, 30, 31, 72],
                    "breaks": ["-Inf", 12, 15, 24, 30, "Inf"]}),
    ("assign_bin", {"x": [250, 1248.2, 1248.3, 1919, 2788.8, 4738, 18424],
                    "breaks": ["-Inf", 1248.2, 1919, 2788.8, 4738, "Inf"]}),
    ("assign_bin", {"x": [0.5, 15857.5, 2000000.5], "breaks": ["-Inf", 15857.5, 1000000, "Inf"]}),
    ("assign_bin", {"x": [4, 2, 1, 4], "breaks": None}),
    ("assign_bin", {"x": ["A11", "A14", "A11"], "breaks": None}),
    ("woe_table", {"bin": ["b", "a", "a", "c", "b", "b"], "y": [0, 1, 0, 0, 1, 0]}),
    ("woe_table", {"bin": ["(12,15]", "[-Inf,12]", "(30, Inf]", "[-Inf,12]"], "y": [1, 0, 0, 0]}),
    ("apply_woe", {"bin": ["A11", "A99", "A12"], "table": _TABLE}),
    ("to_score", {"pd": [1 / 51, 0.5, 0.01, 0.99]}),
    ("auc", {"y": [0, 0, 1, 1, 0, 1], "p": _TIED}),
    ("ks", {"y": [0, 0, 1, 1, 0, 1], "p": _TIED}),
]


def cases() -> list[dict]:
    return [{"id": f"{fn}_{i}", "fn": fn, "args": args} for i, (fn, args) in enumerate(CASES)]


# ---- subprocess helpers ----------------------------------------------------------------------

def _safe_env(root: Path) -> dict:
    """Environment for generated code: no API keys or other secrets."""
    keep = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")}
    return {**keep, "PYTHONHASHSEED": "0", "PYTHONPATH": str(root / "src")}


def _run(cmd: list[str], cwd: Path, env: dict | None, timeout: int) -> str | None:
    """Run a command; return None on success, else the tail of its error output."""
    try:
        p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return f"timed out after {timeout}s"
    return None if p.returncode == 0 else (p.stderr or p.stdout)[-1500:].strip()


def _rscript() -> str:
    exe = shutil.which("Rscript")
    if not exe:
        raise AgentError("Rscript not found: R is needed to check parity")
    return exe


def run_oracle(root: Path, r_path: Path, work: Path, timeout: int) -> dict:
    (work / "cases.json").write_text(json.dumps(cases()))
    err = _run([_rscript(), str(root / "r" / "oracle.R"), str(r_path), str(work / "cases.json"),
                str(work / "oracle.json")], root, None, timeout)
    if err:
        raise AgentError(f"R oracle failed: {err}")
    out = {r["id"]: r for r in json.loads((work / "oracle.json").read_text())}
    bad = [i for i, r in out.items() if "error" in r]
    if bad:
        raise AgentError(f"R oracle errors in cases {bad}")
    return {i: r["result"] for i, r in out.items()}


def make_resampled(data_dir: Path, out: Path, seed: int) -> Path:
    train = pd.read_csv(data_dir / "german_credit_train.csv")
    test = pd.read_csv(data_dir / "german_credit_test.csv")
    df = pd.concat([train, test]).sample(frac=1, random_state=seed).reset_index(drop=True)
    out.mkdir(parents=True, exist_ok=True)
    df.iloc[:len(train)].to_csv(out / "german_credit_train.csv", index=False)
    df.iloc[len(train):].to_csv(out / "german_credit_test.csv", index=False)
    return out


# ---- comparisons -----------------------------------------------------------------------------

def _num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str) and v in ("Inf", "-Inf", "NaN", "Infinity", "-Infinity"):
        return float(v)
    return None


def same(e, g, tol: float) -> bool:
    """Compare an R oracle result with a Python result (JSON-normalised)."""
    if isinstance(e, list) != isinstance(g, list):
        e, g = (e if isinstance(e, list) else [e]), (g if isinstance(g, list) else [g])
    if isinstance(e, list):
        if e and isinstance(e[0], dict) and g and isinstance(g[0], dict) and "bin" in e[0]:
            e = sorted(e, key=lambda r: str(r["bin"]))
            g = sorted(g, key=lambda r: str(r.get("bin")))
        return len(e) == len(g) and all(same(a, b, tol) for a, b in zip(e, g))
    if isinstance(e, dict):
        return isinstance(g, dict) and all(k in g and same(v, g[k], tol)
                                           for k, v in e.items() if not k.startswith("_"))
    if isinstance(e, bool) or isinstance(g, bool):
        return isinstance(e, bool) and isinstance(g, bool) and e == g
    ne, ng = _num(e), _num(g)
    if ne is not None and ng is not None:
        if np.isnan(ne) or np.isnan(ng) or np.isinf(ne) or np.isinf(ng):
            return (np.isnan(ne) and np.isnan(ng)) or ne == ng
        return abs(ne - ng) <= tol
    return e == g


def _short(v, n: int = 160) -> str:
    s = json.dumps(v) if not isinstance(v, str) else v
    return s if len(s) <= n else s[:n] + "..."


def _first_diffs(e, g, tol: float, n: int = 3) -> str:
    """For list results, show only the elements that differ."""
    if not (isinstance(e, list) and isinstance(g, list) and len(e) == len(g)):
        return ""
    idx = [i for i, (a, b) in enumerate(zip(e, g)) if not same(a, b, tol)][:n]
    return "; ".join(f"[{i}] R {_short(e[i], 80)} vs Python {_short(g[i], 80)}" for i in idx)


def run_unit(root: Path, module: Path, work: Path, oracle: dict, tol: float,
             timeout: int) -> tuple[list[dict], str | None]:
    out = work / "unit_results.json"
    err = _run([sys.executable, "-m", "factory.redevelop_harness", str(module),
                str(work.parent / "cases.json"), str(out)], work, _safe_env(root), timeout)
    if err:
        return [], f"unit-test harness failed: {err}"
    res = json.loads(out.read_text())
    if "import_error" in res:
        return [], f"module failed to import: {res['import_error'][-800:]}"
    got = {r["id"]: r for r in res["results"]}
    rows = []
    for c in cases():
        g = got.get(c["id"], {"error": "no result"})
        ok = "error" not in g and same(oracle[c["id"]], g["result"], tol)
        rows.append({"id": c["id"], "fn": c["fn"], "transformation": TRANSFORMATIONS[c["fn"]],
                     "passed": ok, "args": _short(c["args"]), "expected": _short(oracle[c["id"]]),
                     "got": _short(g.get("result", g.get("error"))),
                     "diff": "" if ok or "error" in g else _first_diffs(oracle[c["id"]],
                                                                         g["result"], tol)})
    return rows, None


def _check(split, name, passed, diff=None, tol=None, detail=""):
    return {"split": split, "check": name, "passed": bool(passed),
            "max_abs_diff": None if diff is None else float(diff), "tolerance": tol,
            "detail": detail}


def _maxdiff(a, b) -> float:
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    return float(np.max(np.abs(a - b))) if a.size else 0.0


def _floats(xs) -> list[float]:
    return [float(x) for x in xs]


def score_distribution(scores) -> dict:
    s = np.asarray(scores, dtype=float)
    bands = pd.Series(np.floor(s / 20) * 20).value_counts().sort_index()
    return {"mean": float(s.mean()), "std": float(s.std()),
            "deciles": _floats(np.quantile(s, np.linspace(0.1, 0.9, 9))),
            "bands": {f"{int(k)}-{int(k) + 20}": int(v) for k, v in bands.items()}}


def compare_outputs(r_dir: Path, py_dir: Path, tol: dict, split: str) -> list[dict]:
    try:
        rb = pd.read_csv(r_dir / "bins.csv", dtype={"bin": str}, keep_default_na=False)
        pb = pd.read_csv(py_dir / "bins.csv", dtype={"bin": str}, keep_default_na=False)
        rc, pc = pd.read_csv(r_dir / "coefficients.csv"), pd.read_csv(py_dir / "coefficients.csv")
        rp = pd.read_csv(r_dir / "predictions_test.csv")
        pp = pd.read_csv(py_dir / "predictions_test.csv")
        rm = json.loads((r_dir / "metrics.json").read_text())
        pm = json.loads((py_dir / "metrics.json").read_text())
        out = []
        # bins / WoE / IV
        key = ["variable", "bin"]
        m = rb.merge(pb, on=key, how="outer", suffixes=("_r", "_py"), indicator="side")
        only = m[m["side"] != "both"]
        out.append(_check(split, "bin labels per variable", only.empty, detail="" if only.empty else
                          "mismatched bins: " + "; ".join(
                              f"{r.variable} {r.bin!r} ({'R' if r.side == 'left_only' else 'Python'}"
                              " only)" for r in only.head(6).itertuples())))
        both = m[m["side"] == "both"]
        counts_ok = bool(((both.n_good_r == both.n_good_py) & (both.n_bad_r == both.n_bad_py)).all())
        out.append(_check(split, "good/bad counts per bin", counts_ok and len(both) > 0))
        d = _maxdiff(both.woe_r, both.woe_py)
        out.append(_check(split, "WoE per bin", d <= tol["woe"], d, tol["woe"]))
        d = _maxdiff(both.iv_variable_r, both.iv_variable_py)
        out.append(_check(split, "IV per variable", d <= tol["woe"], d, tol["woe"]))
        rs, ps = list(rm["selected_variables"]), list(pm.get("selected_variables", []))
        out.append(_check(split, "IV selection (selected variables, order)", rs == ps,
                          detail="" if rs == ps else f"R {rs} vs Python {ps}"))
        rbk, pbk = rm.get("breaks", {}), pm.get("breaks", {})
        bk_ok = set(rbk) == set(pbk) and all(
            len(rbk[k]) == len(pbk[k]) and same(_floats(rbk[k]), _floats(pbk[k]), tol["woe"])
            for k in rbk)
        out.append(_check(split, "numeric break points", bk_ok,
                          detail="" if bk_ok else f"R {rbk} vs Python {pbk}"))
        # coefficients
        rt, pt = list(rc.term), list(pc.term)
        out.append(_check(split, "coefficient terms (names, order)", rt == pt,
                          detail="" if rt == pt else f"R {rt} vs Python {pt}"))
        if rt == pt:
            d = _maxdiff(rc.estimate, pc.estimate)
            out.append(_check(split, "coefficient estimates", d <= tol["coefficient"], d,
                              tol["coefficient"]))
            d = _maxdiff(rc.std_error, pc.std_error)
            out.append(_check(split, "coefficient standard errors", d <= tol["std_error"], d,
                              tol["std_error"]))
        # predictions and scores
        ids_ok = list(rp.id) == list(pp.id) and list(rp.bad) == list(pp.bad)
        out.append(_check(split, "prediction rows (id, bad, order)", ids_ok))
        if ids_ok:
            d = _maxdiff(rp.pd, pp.pd)
            out.append(_check(split, "predicted PD", d <= tol["pd"], d, tol["pd"]))
            d = _maxdiff(rp.score, pp.score)
            out.append(_check(split, "score", d <= tol["score"], d, tol["score"]))
            rd, pdist = score_distribution(rp.score), score_distribution(pp.score)
            d = _maxdiff(rd["deciles"], pdist["deciles"])
            out.append(_check(split, "score distribution: deciles", d <= tol["score"], d,
                              tol["score"]))
            out.append(_check(split, "score distribution: 20-point band counts",
                              rd["bands"] == pdist["bands"]))
        # metrics
        for name in ("auc", "gini", "ks", "bad_rate", "n"):
            try:
                d = max(abs(float(rm["metrics"][s][name]) - float(pm["metrics"][s][name]))
                        for s in ("train", "test"))
            except (KeyError, TypeError, ValueError) as e:
                out.append(_check(split, f"{name.upper()} (train, test)", False,
                                  detail=f"missing in Python metrics.json: {e}"))
                continue
            out.append(_check(split, f"{name.upper()} (train, test)", d <= tol["metric"], d,
                              tol["metric"]))
        return out
    except Exception as e:
        return [_check(split, "read and compare outputs", False, detail=f"{type(e).__name__}: {e}")]


def evaluate(root: Path, module: Path, work: Path, oracle: dict, splits: list[tuple],
             rcfg: dict) -> dict:
    """Run every check on one generated module."""
    tol, timeout = rcfg["tolerance"], rcfg["run_timeout_s"]
    res = {"static": tr.static_check(module.read_text(), rcfg["allowed_imports"]),
           "unit": [], "parity": [], "errors": []}
    if not res["static"]:
        res["unit"], err = run_unit(root, module, work, oracle, tol["woe"], timeout)
        if err:
            res["errors"].append(err)
        for split, r_dir, data_dir in splits:
            out_dir = work / "outputs" / split
            err = _run([sys.executable, str(module), str(data_dir), str(out_dir)], work,
                       _safe_env(root), timeout)
            if err:
                res["parity"].append(_check(split, "run main()", False, detail=err))
            else:
                res["parity"] += compare_outputs(r_dir, out_dir, tol, split)
    res["failures"] = (
        [f"static check: {i}" for i in res["static"]] + res["errors"]
        + [f"unit test {u['fn']} ({u['id']}): args {u['args']} -> "
           + (f"differs at {u['diff']}" if u["diff"] else f"R {u['expected']}, Python {u['got']}")
           for u in res["unit"] if not u["passed"]]
        + [f"parity [{p['split']}] {p['check']}: "
           + (p["detail"] or f"max abs diff {p['max_abs_diff']:.3g} > tolerance {p['tolerance']}")
           for p in res["parity"] if not p["passed"]])
    res["passed"] = not res["failures"] and bool(res["unit"]) and bool(res["parity"])
    return res


# ---- report ----------------------------------------------------------------------------------

def _fmt(x) -> str:
    return "" if x is None else (f"{x:.2e}" if isinstance(x, float) else str(x))


def unit_summary(unit: list[dict]) -> dict:
    by = {}
    for u in unit:
        b = by.setdefault(u["fn"], {"transformation": u["transformation"], "passed": 0, "total": 0})
        b["total"] += 1
        b["passed"] += u["passed"]
    return by


def render_report(model_id: str, red: dict, unit: list[dict], parity: list[dict],
                  dist: dict | None) -> str:
    ok = red["passed"]
    out = [f"# Parity report: {model_id} (R -> Python)", "",
           f"**Result: {'PARITY PROVEN' if ok else 'PARITY NOT ACHIEVED'}**"
           + (" - pending Gate 3 review." if ok else ""), "",
           f"- Original: `{red['source']}` (R)",
           f"- Rebuild: `{red['target']}`" + (f" (sha256 `{red['sha256'][:12]}`)" if ok else ""),
           f"- Generated at {red['generated_at']} by the translation agent; checks are deterministic.",
           "- Reproduce: `make test` (runs `tests/test_redeveloped_scorecard.py`), or "
           f"`make run MODEL={model_id}` to re-run the stage (cached replies cost nothing).", "",
           "## Self-healing attempts", "",
           "| # | Model | Escalated | Failures | Tokens | Cost (USD) | Cached |",
           "|---|---|---|---|---|---|---|"]
    for a in red["attempts"]:
        out.append(f"| {a['n']} | {a['model']} | {'yes' if a['escalated'] else 'no'} | "
                   f"{a['n_failures']} | {a['input_tokens'] + a['output_tokens']} | "
                   f"{a['cost_usd']:.4f} | {'yes' if a['cached'] else 'no'} |")
    for a in red["attempts"]:
        if a["failures"]:
            out += ["", f"Attempt {a['n']} failures (fed back to the agent):", ""]
            out += [f"- {f}" for f in a["failures"][:15]]
            if len(a["failures"]) > 15:
                out.append(f"- ... {len(a['failures']) - 15} more")
    out += ["", "## Transformation unit tests (R oracle vs Python, synthetic edge cases)", "",
            "| Transformation | Function | Cases passed |", "|---|---|---|"]
    for fn, b in unit_summary(unit).items():
        out.append(f"| {b['transformation']} | `{fn}` | {b['passed']}/{b['total']} |")
    out += ["", "IV selection and the logistic regression fit are tested end to end below "
            "(selected variables, coefficients).", "",
            "## End-to-end parity", "",
            "`original` = the committed R reference outputs on the fixed train/test split. "
            "`resampled` = a reshuffled split (seed "
            f"{red['resample_seed']}) that the agent never saw, with R re-run on it.", "",
            "| Split | Check | Max abs diff | Tolerance | Pass |", "|---|---|---|---|---|"]
    for p in parity:
        out.append(f"| {p['split']} | {p['check']} | {_fmt(p['max_abs_diff'])} | "
                   f"{_fmt(p['tolerance'])} | {'yes' if p['passed'] else 'NO: ' + p['detail'][:120]} |")
    if dist:
        out += ["", "## Score distribution (original split, test set)", "",
                "| Statistic | R | Python |", "|---|---|---|",
                f"| mean | {dist['r']['mean']:.6f} | {dist['py']['mean']:.6f} |",
                f"| std | {dist['r']['std']:.6f} | {dist['py']['std']:.6f} |"]
        for i, (a, b) in enumerate(zip(dist["r"]["deciles"], dist["py"]["deciles"]), 1):
            out.append(f"| P{i * 10} | {a:.6f} | {b:.6f} |")
        out += ["", "| Score band | R count | Python count |", "|---|---|---|"]
        for band in dist["r"]["bands"]:
            out.append(f"| {band} | {dist['r']['bands'][band]} | "
                       f"{dist['py']['bands'].get(band, 0)} |")
    out += ["", "## Notes", "",
            "- Tolerances are absolute and set in `config/config.yaml` (`redevelop.tolerance`). "
            "Labels, counts, selected variables, term names and row ids must match exactly.",
            "- R's `glm()` and statsmodels both use IRLS but stop at slightly different "
            "convergence points, so coefficients agree to about 1e-8, not bit for bit.",
            "- Generated code runs in a subprocess with a timeout and without API keys in its "
            "environment, after a static import allow-list check.",
            "- Not covered: R-specific behaviour on inputs outside these cases (e.g. missing "
            "values, which German Credit does not contain)."]
    return "\n".join(out) + "\n"


# ---- stage -----------------------------------------------------------------------------------

def _reference_heads(ref_dir: Path) -> dict[str, str]:
    heads = {n: "\n".join((ref_dir / n).read_text().splitlines()[:4]) for n in REF_FILES}
    m = json.loads((ref_dir / "metrics.json").read_text())
    m["selected_variables"] = m["selected_variables"][:2] + ["..."]
    m["breaks"] = dict(list(m["breaks"].items())[:1])
    heads["metrics.json (abridged)"] = json.dumps(m, indent=1)
    return heads


def _rel(path: Path, root: Path) -> str:
    """Repo-relative path for the card and report (absolute if outside the repo, e.g. in tests)."""
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def _attempt_row(n, res_llm, escalated, result) -> dict:
    return {"n": n, "model": res_llm.model, "escalated": escalated, "passed": result["passed"],
            "n_failures": len(result["failures"]), "failures": result["failures"],
            "input_tokens": res_llm.input_tokens, "output_tokens": res_llm.output_tokens,
            "cost_usd": res_llm.cost_usd, "cached": res_llm.cached}


def run_stage(card: dict, ctx) -> dict:
    cfg, rcfg = ctx.cfg, ctx.cfg["redevelop"]
    if card["language"] not in rcfg["source_languages"]:
        return {"applicable": False,
                "summary": f"Not applicable: only {', '.join(rcfg['source_languages'])} models "
                           f"are rebuilt in Python; this model is {card['language']}."}
    root, mid = ctx.model_dir.parent.parent, card["model_id"]
    r_rel = next(f for f in card["source_files"] if f.lower().endswith(".r"))
    r_path, ref_dir, data_dir = root / r_rel, root / "artifacts" / "reference" / mid, \
        root / "data" / "processed"
    work = Path(cfg["paths"]["redevelop_artifacts"]) / mid
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    timeout = rcfg["run_timeout_s"]

    oracle = run_oracle(root, r_path, work, timeout)
    resampled = make_resampled(data_dir, work / "data_resampled", rcfg["resample_seed"])
    err = _run([_rscript(), str(r_path), str(resampled), str(work / "r_resampled")], root, None,
               timeout)
    if err:
        raise AgentError(f"R scorecard failed on the resampled split: {err}")
    splits = [("original", ref_dir, data_dir), ("resampled", work / "r_resampled", resampled)]

    feedback = discover.reviewer_feedback(cfg["paths"]["audit"], mid,
                                          card["stages"][STAGE]["completed_at"], stage=STAGE)
    doc_steps = [{"step": t["step"], "description": t["description"]}
                 for t in (card.get("documentation") or {}).get("transformations", [])]
    base = tr.build_prompt(r_rel, r_path.read_text(), _reference_heads(ref_dir), doc_steps,
                           rcfg["allowed_imports"], feedback)
    escalate_after = cfg["llm"]["max_retries_before_escalation"]

    attempts, code, result, module = [], None, None, None
    for n in range(1, rcfg["max_attempts"] + 1):
        escalated = n > escalate_after
        prompt = base if n == 1 else tr.repair_prompt(base, n - 1, code, result["failures"])
        res = ctx.llm.complete(tr.AGENT, prompt, system=tr.SYSTEM, max_tokens=rcfg["max_tokens"],
                               escalate=escalated, model_id=mid)
        try:
            code = tr.extract_code(res.text)
        except ValueError as e:
            result = {"passed": False, "failures": [str(e)], "unit": [], "parity": []}
        else:
            module = work / f"attempt_{n}.py"
            module.write_text(code)
            att_dir = work / f"attempt_{n}"
            att_dir.mkdir()
            result = evaluate(root, module, att_dir, oracle, splits, rcfg)
        attempts.append(_attempt_row(n, res, escalated, result))
        (work / "attempts.json").write_text(json.dumps(attempts, indent=1))
        if result["passed"]:
            break

    target = Path(cfg["paths"]["redeveloped"]) / mid / (Path(r_rel).stem + ".py")
    report_path = Path(cfg["paths"]["parity_report"])
    red = {"source": r_rel, "target": _rel(target, root), "passed": result["passed"],
           "attempts": attempts, "resample_seed": rcfg["resample_seed"],
           "generated_at": model_card.now_iso(), "reviewer_feedback_used": feedback,
           "report_path": _rel(report_path, root), "sha256": None}
    dist = None
    if result["passed"]:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(module, target)
        red["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
        r_scores = pd.read_csv(ref_dir / "predictions_test.csv").score
        py_scores = pd.read_csv(work / f"attempt_{len(attempts)}" / "outputs" / "original"
                                / "predictions_test.csv").score
        dist = {"r": score_distribution(r_scores), "py": score_distribution(py_scores)}
    unit, parity = result.get("unit", []), result.get("parity", [])
    report = render_report(mid, red, unit, parity, dist)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report)
    (work / "parity.json").write_text(json.dumps(
        {"redevelopment": red, "unit": unit, "parity": parity, "distribution": dist}, indent=1))
    if not result["passed"]:
        raise AgentError(f"no parity after {len(attempts)} attempts; see {red['report_path']}")

    us = unit_summary(unit)
    diffs = {p["check"]: p["max_abs_diff"] for p in parity
             if p["split"] == "original" and p["max_abs_diff"] is not None}
    red["unit_tests"] = {"passed": sum(u["passed"] for u in unit), "total": len(unit),
                         "by_transformation": us}
    red["parity"] = {"passed": sum(p["passed"] for p in parity), "total": len(parity),
                     "max_abs_diff_original": diffs}
    card["redevelopment"] = red
    model_card.validate(card)
    if mlflow.active_run():
        mlflow.log_metrics({f"parity_{k}": v for k, v in {
            "coef_max_abs_diff": diffs.get("coefficient estimates"),
            "pd_max_abs_diff": diffs.get("predicted PD"),
            "score_max_abs_diff": diffs.get("score"),
            "attempts": len(attempts)}.items() if v is not None})

    cost = sum(a["cost_usd"] for a in attempts)
    tokens = sum(a["input_tokens"] + a["output_tokens"] for a in attempts)
    summary = "\n".join([
        f"Python rebuild: {red['target']} (from {r_rel})",
        f"Attempts: {len(attempts)} ("
        + ", ".join(f"{a['model']}{' FAIL' if not a['passed'] else ' PASS'}" for a in attempts) + ")",
        f"Transformation unit tests: {red['unit_tests']['passed']}/{len(unit)} cases passed "
        f"across {len(us)} transformations",
        f"Parity checks: {red['parity']['passed']}/{len(parity)} passed (original + resampled split)",
        "Max abs diff (original): " + ", ".join(
            f"{k} {diffs[k]:.1e}" for k in ("coefficient estimates", "predicted PD", "score",
                                            "AUC (train, test)") if k in diffs),
        f"Parity report: {red['report_path']}",
        f"LLM: {tokens} tokens, ${cost:.4f}",
    ])
    return {"summary": summary}
