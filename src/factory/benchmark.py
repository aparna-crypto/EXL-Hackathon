"""Stage 7 benchmark: time the automated pipeline per model and check its consistency.

Machine time per stage = deterministic work re-executed and timed now (scanner, R oracle + parity
re-check, validation evidence) + the live LLM latency recorded in the ledger for that model and
stage. The manual comparison uses `config/manual_baseline.yaml`, which the user supplies; when it
is empty no speed-up is computed (the factory never invents a manual baseline).
"""
from __future__ import annotations

import copy
import hashlib
import json
import shutil
import tempfile
import time
import warnings
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import yaml  # noqa: E402

from factory import model_card, redevelop, scanner, validate  # noqa: E402
from factory.config import REPO_ROOT  # noqa: E402

STAGES = ["discover", "document", "redevelop", "validate"]
AGENT_STAGE = {"inventory": "discover", "documentation": "document", "translation": "redevelop",
               "report": "validate", "monitor": "monitor"}


def llm_usage(ledger: Path) -> dict:
    """{model_id: {stage: {calls, live_s, tokens, cost_usd}}} from live (non-cached) ledger rows."""
    out: dict = defaultdict(lambda: defaultdict(lambda: {"calls": 0, "live_s": 0.0, "tokens": 0,
                                                         "cost_usd": 0.0}))
    if not ledger.exists():
        return {}
    for line in ledger.read_text().splitlines():
        r = json.loads(line)
        if r["cached"] or not r.get("model_id") or r["agent"] not in AGENT_STAGE:
            continue
        u = out[r["model_id"]][AGENT_STAGE[r["agent"]]]
        u["calls"] += 1
        u["live_s"] += r["duration_s"]
        u["tokens"] += r["input_tokens"] + r["output_tokens"]
        u["cost_usd"] += r["cost_usd"]
    return {m: dict(s) for m, s in out.items()}


def gate_decisions(audit_dir: Path) -> dict:
    """{model_id: {stage: [decision, ...]}} from the append-only gate log."""
    out: dict = defaultdict(lambda: defaultdict(list))
    path = audit_dir / "gate_decisions.jsonl"
    for line in (path.read_text().splitlines() if path.exists() else []):
        e = json.loads(line)
        out[e["model_id"]][e["stage"]].append(e["decision"])
    return {m: dict(s) for m, s in out.items()}


def _timed(fn):
    t0 = time.perf_counter()
    val = fn()
    return time.perf_counter() - t0, val


def time_discover(root: Path, mid: str) -> float:
    return _timed(lambda: scanner.scan_model(root / "models" / mid, root))[0]


def time_redevelop(cfg: dict, root: Path, card: dict, work: Path) -> tuple[float, dict]:
    """Re-run the R oracle, the R model on a reshuffled split and every parity check on the
    approved rebuild (the deterministic part of Stage 4, without the translation agent)."""
    rcfg, mid = cfg["redevelop"], card["model_id"]
    r_path = root / next(f for f in card["source_files"] if f.lower().endswith(".r"))
    module = root / card["redevelopment"]["target"]
    data_dir, ref_dir, timeout = root / "data" / "processed", root / "artifacts" / "reference" / mid, \
        rcfg["run_timeout_s"]

    def go():
        oracle = redevelop.run_oracle(root, r_path, work, timeout)
        resampled = redevelop.make_resampled(data_dir, work / "data_resampled", rcfg["resample_seed"])
        err = redevelop._run([redevelop._rscript(), str(r_path), str(resampled),
                              str(work / "r_resampled")], root, None, timeout)
        if err:
            raise RuntimeError(err)
        splits = [("original", ref_dir, data_dir), ("resampled", work / "r_resampled", resampled)]
        ev_dir = work / "evaluate"  # evaluate() reads cases.json from its parent, as in Stage 4
        ev_dir.mkdir()
        return redevelop.evaluate(root, module, ev_dir, oracle, splits, rcfg)

    elapsed, res = _timed(go)
    return elapsed, {"passed": res["passed"], "unit": len(res["unit"]),
                     "parity": len(res["parity"]),
                     "parity_passed": sum(p["passed"] for p in res["parity"])}


def _validation_run(cfg: dict, root: Path, card: dict, out_dir: Path) -> tuple[float, dict]:
    vcfg = cfg["validate"]
    kind = vcfg["models"][card["model_id"]]
    ev = validate.Evidence(vcfg["thresholds"], out_dir)
    card = copy.deepcopy(card)
    card.pop("monitoring", None)  # the original Stage 5 test set, not a monitoring batch
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore")
        elapsed, _ = _timed(lambda: validate.BUILDERS[kind](card, root, ev, vcfg, cfg["seed"]))
    return elapsed, ev.metrics


def metrics_digest(metrics: dict) -> str:
    return hashlib.sha256(json.dumps(metrics, sort_keys=True, default=str).encode()).hexdigest()[:12]


def time_validate(cfg: dict, root: Path, card: dict, work: Path) -> tuple[float, dict]:
    """Rebuild the validation evidence twice and compare with the stored Stage 5 evidence."""
    t1, m1 = _validation_run(cfg, root, card, work / "run1")
    _, m2 = _validation_run(cfg, root, card, work / "run2")
    stored = Path(cfg["paths"]["validation_artifacts"]) / card["model_id"] / "evidence.json"
    ms = json.loads(stored.read_text())["metrics"] if stored.exists() else None
    m1j = json.loads(json.dumps(m1, default=str))
    return t1, {"digest": metrics_digest(m1j), "repeat_identical": m1 == m2,
                "matches_stored_stage5": None if ms is None else m1j == ms,
                "metrics": sum(len(v) for v in m1.values())}


def consistency_row(card: dict, gates: dict) -> dict:
    inv, doc, val = card.get("inventory", {}), card.get("documentation") or {}, \
        card.get("validation") or {}
    fields = ["owner", "purpose", "model_type", "risk_tier", "last_validation_date",
              "documentation_status"]
    secs = (doc.get("sections") or {}).values()
    cov = doc.get("coverage") or {}
    return {
        "model_id": card["model_id"], "language": card["language"],
        "schema_version": card["schema_version"],
        "inventory_fields_found": sum(inv.get(f) not in (None, "", "unknown") for f in fields),
        "inventory_fields": len(fields),
        "inventory_quotes": len(inv.get("evidence") or {}),
        "gaps": len(card.get("gaps") or []),
        "doc_sections": len(doc.get("sections") or {}),
        "doc_high_confidence": sum(s.get("confidence") == "high" for s in secs),
        "doc_claims_dropped": len(doc.get("rejected") or []),
        "doc_unknowns": len(doc.get("unknowns") or []),
        "variable_coverage": (f"{cov['documented']}/{cov['model_terms']}" if cov.get("available")
                              else "n/a (not executed)"),
        "validation_checks": len(val.get("checks") or []) if val else None,
        "validation_overall": val.get("overall_rag") if val else None,
        "narrative_dropped": len(val.get("narrative_dropped") or []) if val else None,
        "gate_decisions": sum(len(v) for v in gates.get(card["model_id"], {}).values()),
        "gate_rejections": sum(v.count("reject") for v in gates.get(card["model_id"], {}).values()),
        "stage_status": {s: card["stages"][s]["status"] for s in STAGES},
    }


def load_baseline(path: Path) -> dict:
    return yaml.safe_load(path.read_text()) if path.exists() else {}


def manual_hours(baseline: dict, mid: str, stage: str):
    per = (baseline.get("models") or {}).get(mid) or {}
    v = per.get(stage, (baseline.get("activities") or {}).get(stage))
    return None if v is None else float(v)


def compare(rows: list[dict], baseline: dict) -> list[dict]:
    """Automated vs manual per model and stage, only where the user supplied a manual figure."""
    review_min = baseline.get("review_minutes_per_gate")
    out = []
    for r in rows:
        for s in STAGES:
            st = r["stages"][s]
            if not st["ran"]:
                continue
            manual = manual_hours(baseline, r["model_id"], s)
            human_h = None if review_min is None else review_min / 60 * max(1, st["gate_decisions"])
            auto_h = st["machine_s"] / 3600 + (human_h or 0)
            out.append({"model_id": r["model_id"], "stage": s, "machine_s": round(st["machine_s"], 1),
                        "review_h": None if human_h is None else round(human_h, 2),
                        "automated_h": round(auto_h, 3), "manual_h": manual,
                        "time_saved_pct": (None if manual is None or not manual
                                           else round(100 * (1 - auto_h / manual), 1))})
    return out


def _chart(rows: list[dict], path: Path) -> None:
    fig, ax = plt.subplots(figsize=(9, 4))
    names = [r["model_id"] for r in rows]
    left = [0.0] * len(rows)
    colors = {"discover": "#4c78a8", "document": "#f58518", "redevelop": "#54a24b",
              "validate": "#b279a2"}
    for s in STAGES:
        for part, hatch in (("deterministic_s", ""), ("llm_s", "//")):
            vals = [r["stages"][s][part] for r in rows]
            ax.barh(names, vals, left=left, color=colors[s], hatch=hatch, edgecolor="white",
                    label=f"{s} ({'LLM' if hatch else 'code'})")
            left = [a + b for a, b in zip(left, vals)]
    ax.set_xlabel("machine time per model (seconds, excludes human review)")
    ax.legend(fontsize=7, ncol=4, loc="lower right")
    ax.invert_yaxis()
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=110)
    plt.close(fig)


def _md(result: dict) -> str:
    L = ["# Benchmark (Stage 7)", "",
         f"Generated {result['generated_at']} by `make benchmark`. Machine time = deterministic "
         "work re-executed and timed in this run + live LLM latency recorded in the ledger "
         "(all live calls for that model and stage, including failed attempts and reruns "
         "triggered by gate rejections). Human review time is not measured.", "",
         "## Machine time per model (seconds)", "",
         "| Model | " + " | ".join(STAGES) + " | Total | LLM calls | LLM cost |",
         "|---|" + "---|" * (len(STAGES) + 3)]
    for r in result["models"]:
        cells = []
        for s in STAGES:
            st = r["stages"][s]
            cells.append(f"{st['machine_s']:.1f} ({st['deterministic_s']:.1f} code + "
                         f"{st['llm_s']:.1f} LLM)" if st["ran"] else "-")
        L.append(f"| {r['model_id']} | " + " | ".join(cells) +
                 f" | {r['machine_s']:.1f} | {r['llm_calls']} | ${r['llm_cost_usd']:.3f} |")
    L += ["", "![machine time](../artifacts/benchmark/machine_time.png)", "",
          "## Manual comparison", ""]
    if not result["comparison"] or all(c["manual_h"] is None for c in result["comparison"]):
        L += ["**Manual baseline not supplied.** Fill in `config/manual_baseline.yaml` (hours per "
              "activity, the source of the figures and review minutes per gate) and re-run "
              "`make benchmark`. No speed-up is reported until then.", ""]
    else:
        L += [f"**Source of manual figures: {result['baseline_source'] or 'not stated'}**", ""]
        L += [f"- {k}: {v}" for k, v in result.get("baseline_basis", {}).items()] + [""]
        L += [
              "| Model | Stage | Machine (s) | Review (h) | Automated (h) | Manual (h) | Time saved |",
              "|---|---|---|---|---|---|---|"]
        for c in result["comparison"]:
            L.append(f"| {c['model_id']} | {c['stage']} | {c['machine_s']} | "
                     f"{'-' if c['review_h'] is None else c['review_h']} | {c['automated_h']} | "
                     f"{'-' if c['manual_h'] is None else c['manual_h']} | "
                     f"{'-' if c['time_saved_pct'] is None else str(c['time_saved_pct']) + '%'} |")
        L.append("")
    L += ["## Consistency across models", "",
          "Every model goes through the same schema, scanner, agents, evidence check and "
          "thresholds. The table shows what each produced.", "",
          "| Model | Lang | Inventory fields found | Quotes | Gaps | Doc sections (high conf.) | "
          "Claims dropped | Unknowns | Variable coverage | Validation checks | Overall | "
          "Gate decisions (rejections) |", "|" + "---|" * 12]
    for c in result["consistency"]:
        L.append(f"| {c['model_id']} | {c['language']} | {c['inventory_fields_found']}/"
                 f"{c['inventory_fields']} | {c['inventory_quotes']} | {c['gaps']} | "
                 f"{c['doc_sections']} ({c['doc_high_confidence']}) | {c['doc_claims_dropped']} | "
                 f"{c['doc_unknowns']} | {c['variable_coverage']} | "
                 f"{c['validation_checks'] if c['validation_checks'] is not None else '-'} | "
                 f"{(c['validation_overall'] or '-').upper()} | {c['gate_decisions']} "
                 f"({c['gate_rejections']}) |")
    L += ["", "## Reproducibility", ""]
    for r in result["models"]:
        v, d = r["checks"].get("validate"), r["checks"].get("redevelop")
        if d:
            L.append(f"- {r['model_id']} parity re-check: {d['parity_passed']}/{d['parity']} parity "
                     f"checks and {d['unit']} unit cases, passed = {d['passed']}.")
        if v:
            L.append(f"- {r['model_id']} validation evidence rebuilt twice: identical = "
                     f"{v['repeat_identical']}; equal to the stored Stage 5 evidence = "
                     f"{v['matches_stored_stage5']} ({v['metrics']} metrics, digest {v['digest']}).")
    L += ["", "LLM outputs are not re-generated here: they are cached by prompt hash, so a rerun "
          "replays the approved text for free. Without the cache the documentation agent can "
          "phrase things differently; the evidence check and the gates are the consistency "
          "control for that part.", ""]
    return "\n".join(L)


def run(cfg: dict, root: Path = REPO_ROOT) -> dict:
    paths = cfg["paths"]
    usage = llm_usage(Path(paths["llm_ledger"]))
    gates = gate_decisions(Path(paths["audit"]))
    rows, consistency = [], []
    work_root = Path(tempfile.mkdtemp(prefix="benchmark_"))
    try:
        for card in model_card.list_cards(paths["model_cards"]):
            mid = card["model_id"]
            det = {s: 0.0 for s in STAGES}
            ran = {s: card["stages"][s]["status"] == "approved" for s in STAGES}
            ran["discover"] = card["stages"]["discover"]["status"] != "not_started"
            ran["document"] = card["stages"]["document"]["status"] != "not_started"
            checks = {}
            det["discover"] = time_discover(root, mid)
            if ran["redevelop"]:
                w = work_root / mid / "redevelop"
                w.mkdir(parents=True)
                det["redevelop"], checks["redevelop"] = time_redevelop(cfg, root, card, w)
            if ran["validate"]:
                det["validate"], checks["validate"] = time_validate(cfg, root, card,
                                                                    work_root / mid / "validate")
            u = usage.get(mid, {})
            stages = {}
            for s in STAGES:
                llm_s = u.get(s, {}).get("live_s", 0.0)
                stages[s] = {"ran": ran[s], "deterministic_s": round(det[s], 2),
                             "llm_s": round(llm_s, 2), "machine_s": round(det[s] + llm_s, 2),
                             "llm_calls": u.get(s, {}).get("calls", 0),
                             "gate_decisions": len(gates.get(mid, {}).get(s, []))}
            rows.append({"model_id": mid, "language": card["language"], "stages": stages,
                         "machine_s": round(sum(v["machine_s"] for v in stages.values()), 1),
                         "llm_calls": sum(v["calls"] for k, v in u.items() if k in STAGES),
                         "llm_cost_usd": round(sum(v["cost_usd"] for k, v in u.items()
                                                   if k in STAGES), 4),
                         "checks": checks})
            consistency.append(consistency_row(card, gates))
    finally:
        shutil.rmtree(work_root, ignore_errors=True)

    baseline = load_baseline(root / cfg["benchmark"]["manual_baseline"])
    result = {"generated_at": model_card.now_iso(), "models": rows, "consistency": consistency,
              "baseline_source": baseline.get("source"),
              "baseline_basis": baseline.get("basis") or {},
              "comparison": compare(rows, baseline)}
    out = Path(paths["benchmark_artifacts"])
    out.mkdir(parents=True, exist_ok=True)
    (out / "benchmark.json").write_text(json.dumps(result, indent=1, default=str))
    _chart(rows, out / "machine_time.png")
    report = Path(paths["benchmark_report"])
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(_md(result))
    return result
