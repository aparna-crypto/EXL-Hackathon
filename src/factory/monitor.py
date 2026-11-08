"""Stage 6: post-approval monitoring with drift-triggered re-validation.

For a model whose Gate 4 is approved, simulates monthly production batches (resampled from the
test set, with configurable drift injected from a given month), and computes per month: score PSI
against the development sample, characteristic stability (CSI), calibration and AUC. Months are
rated with the Stage 5 thresholds. A red month, or N amber months in a row, triggers
re-validation: Stage 5 is re-run on the breaching batch and Gate 4 re-opens for a human decision.
The monitor agent only writes the alert narrative ([[fact]] placeholders, see agents/monitor.py).
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import mlflow  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from factory import gates, model_card, validate  # noqa: E402
from factory.agents import AgentError  # noqa: E402
from factory.agents import monitor as monitor_agent  # noqa: E402

# (key, label, threshold key in validate.thresholds, higher is better)
METRICS = [("score_psi", "Score PSI vs development", "psi_max", False),
           ("max_csi", "Largest characteristic CSI", "psi_max", False),
           ("calib_dev", "Calibration |mean PD / bad rate - 1|", "calib_ratio_dev_max", False),
           ("auc", "AUC", "auc_min", True)]


def make_batch(pool: pd.DataFrame, month: int, mcfg: dict, seed: int) -> pd.DataFrame:
    """One simulated month: resample the pool; from drift start, shift the intake."""
    d = mcfg["drift"]
    k = max(0, month - d["start_month"] + 1)
    rng = np.random.default_rng(seed + month)
    w = np.exp(d["risk_tilt_per_month"] * k * pool["bad"].to_numpy())
    idx = rng.choice(len(pool), size=mcfg["batch_size"], replace=True, p=w / w.sum())
    b = pool.iloc[idx].reset_index(drop=True).copy()
    if k:
        b["credit_amount"] = np.round(
            b["credit_amount"] * (1 + d["amount_inflation_per_month"] * k)).astype(int)
        longer = rng.random(len(b)) < min(1.0, d["long_term_share_per_month"] * k)
        b.loc[longer, "duration"] = np.minimum(b.loc[longer, "duration"]
                                               + d["long_term_extra_months"], d["duration_cap"])
    b.insert(0, "month", month)
    b["drift_k"] = k
    return b


def first_trigger(month_rags: list[str], consecutive: int) -> int | None:
    """Index of the first month that triggers re-validation (red, or N ambers in a row)."""
    run = 0
    for i, r in enumerate(month_rags):
        if r == "red":
            return i
        run = run + 1 if r == "amber" else 0
        if run >= consecutive:
            return i
    return None


def _gate4(card: dict, audit: str) -> str:
    e = card["stages"]["validate"]
    if e["status"] in ("approved", "not_applicable", "not_started"):
        return e["status"]
    return gates.status(audit, card["model_id"], "validate", since=e["completed_at"])


def _log_event(audit: str, event: dict) -> None:
    path = Path(audit) / "monitoring_events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps({"timestamp": model_card.now_iso(), **event}) + "\n")


def _dashboard(df: pd.DataFrame, th: dict, start: int, trig_month: int | None, path: Path) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(10, 7))
    for ax, (key, label, tkey, _) in zip(axes.flat, METRICS):
        ax.plot(df.month, df[key], marker="o", color="#1f3b57")
        for lim, col in zip(th[tkey], ("#d9a400", "#c0392b")):
            ax.axhline(lim, ls="--", color=col, lw=1)
        ax.axvspan(start - 0.5, df.month.max() + 0.5, color="#f4e1d2", alpha=0.4, lw=0)
        if trig_month is not None:
            ax.axvline(trig_month, color="#c0392b", lw=2, alpha=0.6)
        ax.set(title=label, xlabel="month")
    fig.suptitle("Monitoring: shaded = injected drift, dashed = amber/red limits, "
                 "red line = re-validation trigger", fontsize=9)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=110)
    plt.close(fig)


def _markdown(card: dict, mon: dict, df: pd.DataFrame) -> str:
    cols = ["month", "drift_k", "n", "bad_rate", "mean_pd", "score_psi", "max_csi",
            "max_csi_var", "calib_dev", "auc", "rag"]
    head = "| " + " | ".join(cols) + " |\n|" + "---|" * len(cols) + "\n"
    body = "".join("| " + " | ".join(validate.fmt(r[c]) if not isinstance(r[c], str) else r[c]
                                     for c in cols) + " |\n" for r in df.to_dict("records"))
    t, a, rv = mon["trigger"], mon.get("alert") or {}, mon.get("revalidation") or {}
    lines = [f"# Monitoring report: {card['name']}", "",
             f"Generated {mon['generated_at']}. Baseline: {mon['baseline']}", "",
             f"Simulation: {mon['simulation']}", "",
             "Thresholds (green/amber limits, from `validate.thresholds`): "
             + ", ".join(f"{k} {v}" for k, v in mon["thresholds"].items()), "",
             f"![dashboard](../../{mon['dashboard']})", "", head + body]
    if t:
        lines += [f"## Trigger: month {t['month']} ({t['rag'].upper()})", "", t["reason"], ""]
        if a:
            lines += ["### Alert (monitor agent, numbers filled in from evidence)", "",
                      a["alert"], "", "Likely drivers:",
                      *[f"- {x}" for x in a["likely_drivers"]], "", "Recommended actions:",
                      *[f"- {x}" for x in a["recommended_actions"]], ""]
        lines += [f"## Re-validation: {rv.get('status', 'n/a')}", "",
                  f"Report: `{rv.get('report_html')}`. Previous validation: "
                  f"{rv.get('previous_validation')}", ""]
    else:
        lines += ["## No trigger", "", "Every month is within the re-validation rule.", ""]
    return "\n".join(lines)


def run(card: dict, ctx) -> dict:
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", "invalid value encountered in cast", RuntimeWarning)
        return _run(card, ctx)


def _run(card: dict, ctx) -> dict:
    cfg, mcfg = ctx.cfg, ctx.cfg["monitor"]
    mid, audit = card["model_id"], cfg["paths"]["audit"]
    th = cfg["validate"]["thresholds"]
    if mid not in mcfg["models"]:
        raise AgentError(f"no monitoring configured for {mid} (config monitor.models)")
    prev = dict((card.get("monitoring") or {}).get("revalidation") or {})
    gate4 = _gate4(card, audit)
    if gate4 != "approved" and prev.get("status") != "pending":
        raise AgentError(f"monitoring needs an approved Gate 4 for {mid} (status: {gate4})")
    if prev.get("status") == "pending" and gate4 == "approved":
        prev.update(status="approved", closed_at=model_card.now_iso())
        _log_event(audit, {"model_id": mid, "event": "revalidation_approved",
                           "month": prev["month"], "card_version": card["card_version"]})

    root = ctx.model_dir.parent.parent
    red = card["redevelopment"]
    mod = validate._load_module(root / red["target"])
    out_dir = Path(cfg["paths"]["monitoring_artifacts"]) / mid
    model_out = out_dir / "model_outputs"
    data = root / "data" / "processed"
    mod.main(str(data), str(model_out))
    train = pd.read_csv(data / "german_credit_train.csv")
    pool = pd.read_csv(data / "german_credit_test.csv")
    _, s_tr, b_tr = validate._score_scorecard(mod, train, model_out)
    bins, seed = cfg["validate"]["psi_bins"], cfg["seed"]

    rows, batches = [], {}
    for m in range(1, mcfg["months"] + 1):
        b = make_batch(pool, m, mcfg, seed)
        batches[m] = b
        p, s, binned = validate._score_scorecard(mod, b, model_out)
        y = b["bad"].to_numpy()
        csi = {v: validate.psi(b_tr[v], binned[v], bins)[0] for v in b_tr.columns}
        top = max(csi, key=csi.get)
        r = {"month": m, "drift_k": int(b.drift_k.iloc[0]), "n": len(b),
             "bad_rate": float(y.mean()), "mean_pd": float(p.mean()),
             "score_psi": validate.psi(s_tr, s, bins)[0], "max_csi": csi[top],
             "max_csi_var": top, "calib_dev": abs(float(p.mean()) / float(y.mean()) - 1),
             "auc": validate.auc(y, p), "csi": csi}
        for key, _, tkey, higher in METRICS:
            r[f"rag_{key}"] = validate.rag(r[key], th[tkey], higher)
        r["rag"] = validate.worst(r[f"rag_{k}"] for k, *_ in METRICS)
        rows.append(r)
        if mlflow.active_run():
            mlflow.log_metrics({f"monitor.{k}": float(r[k]) for k, *_ in METRICS}, step=m)
    df = pd.DataFrame(rows)

    ti = first_trigger(list(df.rag), mcfg["trigger"]["consecutive_amber"])
    trigger = None
    if ti is not None:
        t = rows[ti]
        breaches = [f"{label} {validate.fmt(t[k])} ({t[f'rag_{k}'].upper()}, limits {th[tk]})"
                    for k, label, tk, _ in METRICS if t[f"rag_{k}"] != "green"]
        rule = ("red month" if t["rag"] == "red" else
                f"{mcfg['trigger']['consecutive_amber']} consecutive amber months")
        trigger = {"month": t["month"], "rag": t["rag"], "rule": rule,
                   "reason": f"month {t['month']} rated {t['rag'].upper()} ({rule}): "
                             + "; ".join(breaches)}
    dash = out_dir / "drift_dashboard.png"
    _dashboard(df, th, mcfg["drift"]["start_month"], trigger and trigger["month"], dash)
    metrics_path = out_dir / "monthly_metrics.csv"
    df.drop(columns="csi").to_csv(metrics_path, index=False)

    rel = lambda p: validate._rel(Path(p), root)  # noqa: E731
    report_path = Path(cfg["paths"]["monitoring_reports"]) / f"{mid}.md"
    d = mcfg["drift"]
    mon = {"generated_at": model_card.now_iso(),
           "baseline": f"development (train) scores of the Gate 3/4-approved rebuild "
                       f"{red['target']}, {len(train)} rows",
           "simulation": f"{mcfg['months']} monthly batches of {mcfg['batch_size']} rows "
                         f"resampled from the test set (seed {seed} + month). SYNTHETIC drift "
                         f"from month {d['start_month']}: credit_amount x(1 + "
                         f"{d['amount_inflation_per_month']}k), a share {d['long_term_share_per_month']}k "
                         f"of loans {d['long_term_extra_months']} months longer, riskier intake (weight exp({d['risk_tilt_per_month']}k*bad)). "
                         "Outcomes are assumed observed at batch time (no maturity lag).",
           "thresholds": {tk: th[tk] for _, _, tk, _ in METRICS},
           "months": json.loads(df.drop(columns="csi").to_json(orient="records")),
           "trigger": trigger, "dashboard": rel(dash), "metrics_csv": rel(metrics_path),
           "report": rel(report_path),
           "alert": (card.get("monitoring") or {}).get("alert"), "revalidation": prev or None}

    action = "no trigger: every month within the re-validation rule"
    if trigger and prev.get("month") == trigger["month"]:
        action = (f"month {trigger['month']} breach already triggered re-validation at "
                  f"{prev['triggered_at']} (status: {prev['status']}); not re-triggered")
    elif trigger:
        tm = trigger["month"]
        batch_path = out_dir / f"batch_month_{tm:02d}.csv"
        batches[tm].to_csv(batch_path, index=False)
        t = rows[ti]
        facts = {f"m{r['month']:02d}.{k}": validate.fmt(r[k]) for r in rows for k, *_ in METRICS}
        facts.update({f"m{tm:02d}.csi.{v}": validate.fmt(c) for v, c in t["csi"].items()})
        facts.update({f"m{tm:02d}.bad_rate": validate.fmt(t["bad_rate"]),
                      f"m{tm:02d}.mean_pd": validate.fmt(t["mean_pd"]),
                      "trigger.month": str(tm), "drift.start_month": str(d["start_month"])})
        facts.update({f"limit.{tk}.{lvl}": str(th[tk][i]) for _, _, tk, _ in METRICS
                      for i, lvl in enumerate(("green", "amber"))})
        context = {"model": card["name"], "model_id": mid, "trigger": trigger,
                   "month_ratings": {f"m{r['month']:02d}": {k: r[f"rag_{k}"] for k, *_ in METRICS}
                                     for r in rows},
                   "largest_csi_variable_by_month": {f"m{r['month']:02d}": r["max_csi_var"]
                                                     for r in rows},
                   "simulation": mon["simulation"]}
        mon["alert"] = monitor_agent.run(ctx.llm, mid, context, facts, mcfg["max_tokens"])
        v = card.get("validation") or {}
        prev = {"status": "pending", "month": tm, "reason": trigger["reason"],
                "data_path": rel(batch_path), "triggered_at": model_card.now_iso(),
                "previous_validation": {k: v.get(k) for k in ("generated_at", "overall_rag",
                                                              "report_html")}}
        mon["revalidation"] = prev
        card["monitoring"] = mon
        _log_event(audit, {"model_id": mid, "event": "revalidation_triggered", "month": tm,
                           "reason": trigger["reason"], "card_version": card["card_version"]})
        out = validate.run_stage(card, ctx)
        prev["report_html"] = card["validation"]["report_html"]
        prev["overall_rag"] = card["validation"]["overall_rag"]
        card["stages"]["validate"].update(status="awaiting_review",
                                          completed_at=model_card.now_iso(),
                                          summary=out["summary"])
        action = (f"re-validation run on month {tm} batch: overall "
                  f"{prev['overall_rag'].upper()}; Gate 4 re-opened for review")

    card["monitoring"] = mon
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(_markdown(card, mon, df))
    model_card.save(card, cfg["paths"]["model_cards"], f"monitor: {action}")

    alert = mon.get("alert") or {}
    llm = alert.get("llm") or {}
    summary = "\n".join([
        f"Monitoring report: {mon['report']}; dashboard: {mon['dashboard']}",
        "Months: " + " ".join(f"m{r['month']}={r['rag'][0].upper()}" for r in rows),
        f"Trigger: {trigger['reason'] if trigger else 'none'}",
        f"Action: {action}",
        *([f"Monitor agent: {llm['model']}, {llm['input_tokens'] + llm['output_tokens']} tokens, "
           f"${llm['cost_usd']:.4f}{' (cached)' if llm['cached'] else ''}; sentences dropped: "
           f"{len(alert['dropped_sentences'])}"] if llm else []),
    ])
    return {"summary": summary, "trigger": trigger, "action": action}
