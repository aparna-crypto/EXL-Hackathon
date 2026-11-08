"""Streamlit UI: inventory, gate approvals, audit log, LLM spend.

Run with `make ui`. Drift results come from `make monitor MODEL=<name>`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402
import streamlit.components.v1 as components  # noqa: E402

from factory import discover, gates, model_card, tracking  # noqa: E402
from factory.config import load_config  # noqa: E402

cfg = load_config()
CARDS, AUDIT = cfg["paths"]["model_cards"], cfg["paths"]["audit"]

st.set_page_config(page_title="Model Risk Factory", layout="wide")
st.title("Agentic Model Risk Factory")
page = st.sidebar.radio("View", ["Inventory", "Gate review", "Audit log", "LLM spend",
                                 "Validation reports", "Drift monitoring"])
cards = model_card.list_cards(CARDS)


def stage_status(card: dict, stage: str) -> str:
    e = card["stages"][stage]
    if e["status"] in ("approved", "not_applicable", "not_started"):
        return e["status"]
    gate = gates.status(AUDIT, card["model_id"], stage, since=e["completed_at"])
    return {"pending": "awaiting_review"}.get(gate, gate)


def show_validation(card: dict, height: int = 900) -> None:
    val = card["validation"]
    st.markdown(f"**Overall rating: {val['overall_rag'].upper()}** - {val['outcome']}")
    st.dataframe(pd.DataFrame([{k: c[k] for k in ("section", "check", "value", "rule", "rag",
                                                  "note")} for c in val["checks"]]),
                 use_container_width=True, hide_index=True)
    for key, mime in (("report_html", "text/html"), ("report_pdf", "application/pdf")):
        f = Path(val[key]) if Path(val[key]).is_absolute() else ROOT / val[key]
        if f.exists():
            st.download_button(f"Download {f.suffix[1:].upper()} report", f.read_bytes(),
                               file_name=f.name, mime=mime, key=f"{card['model_id']}-{key}")
    html = Path(val["report_html"])
    html = html if html.is_absolute() else ROOT / html
    if html.exists():
        components.html(html.read_text(), height=height, scrolling=True)


if page == "Inventory":
    if not cards:
        st.info("No model cards yet. Run `make run MODEL=<name>`.")
    else:
        rows = [{**r, "card_version": c["card_version"],
                 **{f"gate:{s}": stage_status(c, s) for s in c["stages"]}}
                for r, c in zip(discover.inventory_rows(cards), cards)]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        st.subheader("Gaps flagged by Discover")
        for c in cards:
            with st.expander(f"{c['model_id']}: {len(c.get('gaps', []))} gap(s)"):
                for g in c.get("gaps", []) or ["none"]:
                    st.write(f"- {g}")
        if any(c.get("discovery") for c in cards):
            st.subheader("Dependency map")
            st.graphviz_chart(discover.to_dot(cards), use_container_width=True)

elif page == "Gate review":
    if not cards:
        st.info("No model cards yet.")
    else:
        model_id = st.selectbox("Model", [c["model_id"] for c in cards])
        card = next(c for c in cards if c["model_id"] == model_id)
        st.table(pd.DataFrame([{"stage": s, "status": stage_status(card, s),
                                "summary": card["stages"][s]["summary"]} for s in card["stages"]]))
        pending = [s for s in card["stages"] if stage_status(card, s) == "awaiting_review"]
        if not pending:
            st.success("Nothing awaiting review for this model.")
        else:
            stage = pending[0]
            st.subheader(f"Review gate: {stage}")
            st.text(card["stages"][stage]["summary"])
            if stage == "discover" and card.get("discovery"):
                ev = card["inventory"].get("evidence", {})
                st.markdown("**Inventory fields and the source quote behind each**")
                st.dataframe(pd.DataFrame([
                    {"field": k, "value": card["inventory"].get(k) if k != "name" else card["name"],
                     "quote": ev.get(k, {}).get("evidence"), "source": ev.get(k, {}).get("source")}
                    for k in ("name", "model_ref", "owner", "purpose", "model_type", "risk_tier",
                              "last_validation_date", "documentation_date")]),
                    use_container_width=True, hide_index=True)
                if ev.get("risk_tier", {}).get("rationale"):
                    st.caption(f"Risk tier rationale: {ev['risk_tier']['rationale']}")
                findings = card["discovery"].get("documentation_findings", [])
                if findings:
                    st.markdown("**Documentation vs code contradictions**")
                    st.dataframe(pd.DataFrame(findings), use_container_width=True, hide_index=True)
                rejected = card["discovery"].get("rejected_agent_answers", [])
                if rejected:
                    st.markdown("**Agent answers rejected by the evidence check**")
                    st.dataframe(pd.DataFrame(rejected), use_container_width=True, hide_index=True)
            if stage == "document" and card.get("documentation"):
                doc = card["documentation"]
                st.markdown("**Section confidence after the evidence check**")
                st.dataframe(pd.DataFrame([
                    {"section": k, "confidence": v["confidence"], "flag": v["flag"],
                     "quotes": len(v["evidence"])} for k, v in doc["sections"].items()]),
                    use_container_width=True, hide_index=True)
                cov = doc["coverage"]
                st.caption("Coverage vs fitted model: " + (
                    f"{cov['documented']}/{cov['model_terms']} variables documented; missing: "
                    f"{', '.join(cov['missing']) or 'none'}" if cov["available"] else cov["note"]))
                if doc["rejected"]:
                    st.markdown("**Agent claims dropped by the evidence check**")
                    st.dataframe(pd.DataFrame(doc["rejected"]), use_container_width=True,
                                 hide_index=True)
                doc_file = Path(doc["doc_path"])
                doc_file = doc_file if doc_file.is_absolute() else ROOT / doc_file
                if doc_file.exists():
                    with st.expander("Draft documentation", expanded=True):
                        st.markdown(doc_file.read_text())
            if stage == "redevelop" and card.get("redevelopment"):
                red = card["redevelopment"]
                st.markdown("**Self-healing attempts**")
                st.dataframe(pd.DataFrame([{k: a[k] for k in ("n", "model", "escalated", "passed",
                                                              "n_failures", "cost_usd", "cached")}
                                           for a in red["attempts"]]),
                             use_container_width=True, hide_index=True)
                st.markdown("**Transformation unit tests (R oracle vs Python)**")
                st.dataframe(pd.DataFrame([{"function": k, **v} for k, v in
                                           red["unit_tests"]["by_transformation"].items()]),
                             use_container_width=True, hide_index=True)
                st.caption(f"Parity checks passed: {red['parity']['passed']}/"
                           f"{red['parity']['total']} (original + resampled split)")
                for label, rel in (("Parity report", red["report_path"]),
                                   ("Generated Python code", red["target"])):
                    f = Path(rel) if Path(rel).is_absolute() else ROOT / rel
                    if f.exists():
                        with st.expander(label, expanded=label == "Parity report"):
                            if f.suffix == ".py":
                                st.code(f.read_text(), language="python")
                            else:
                                st.markdown(f.read_text())
            if stage == "validate" and card.get("validation"):
                show_validation(card)
            with st.expander("Model card JSON"):
                st.json(card)
            with st.form("gate"):
                decision = st.radio("Decision", gates.DECISIONS, horizontal=True)
                reviewer = st.text_input("Reviewer")
                reason = st.text_area("Reason")
                if st.form_submit_button("Submit decision"):
                    try:
                        rec = gates.record_decision(AUDIT, model_id, stage, decision, reviewer,
                                                    reason, card["card_version"],
                                                    tuple(cfg["gates"]["reason_required_for"]))
                        tracking.configure(cfg)
                        tracking.log_gate_decision(rec)
                        st.success(f"Logged: {decision} by {rec['reviewer']} at {rec['timestamp']}. "
                                   "Re-run `make run` to continue the pipeline.")
                    except gates.GateError as e:
                        st.error(str(e))

elif page == "Audit log":
    log = gates.history(AUDIT)
    st.dataframe(pd.DataFrame(log) if log else pd.DataFrame(), use_container_width=True)

elif page == "LLM spend":
    path = Path(cfg["paths"]["llm_ledger"])
    rows = [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []
    b = cfg["llm"]["budget"]
    spent = sum(r["cost_usd"] for r in rows if r["session_id"] == b["session_id"])
    st.metric(f"Spend in session '{b['session_id']}'", f"${spent:.4f}", f"cap ${b['session_usd']:.2f}",
              delta_color="off")
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True)

elif page == "Validation reports":
    validated = [c for c in cards if c.get("validation")]
    if not validated:
        st.info("No validation reports yet: run Stage 5 (`make run MODEL=<name>`).")
    else:
        model_id = st.selectbox("Model", [c["model_id"] for c in validated])
        card = next(c for c in validated if c["model_id"] == model_id)
        st.caption(f"Gate 4 status: {stage_status(card, 'validate')}")
        show_validation(card, height=1400)

else:
    monitored = [c for c in cards if c.get("monitoring")]
    if not monitored:
        st.info("No monitoring results yet: run `make monitor MODEL=<name>` for a model whose "
                "Gate 4 is approved.")
    else:
        model_id = st.selectbox("Model", [c["model_id"] for c in monitored])
        card = next(c for c in monitored if c["model_id"] == model_id)
        mon = card["monitoring"]
        st.caption(f"Generated {mon['generated_at']}. Baseline: {mon['baseline']}")
        st.caption(f"Simulation: {mon['simulation']}")
        t, rv = mon.get("trigger"), mon.get("revalidation") or {}
        if t:
            st.error(f"Re-validation trigger: {t['reason']}")
        else:
            st.success("No trigger: every month is within the re-validation rule.")
        dash = ROOT / mon["dashboard"]
        if dash.exists():
            st.image(str(dash), use_container_width=True)
        st.dataframe(pd.DataFrame(mon["months"]), use_container_width=True, hide_index=True)
        alert = mon.get("alert")
        if t and alert:
            st.subheader("Alert (monitor agent; numbers filled in from evidence)")
            st.write(alert["alert"])
            st.markdown("**Likely drivers**\n" + "\n".join(f"- {x}" for x in alert["likely_drivers"]))
            st.markdown("**Recommended actions**\n"
                        + "\n".join(f"- {x}" for x in alert["recommended_actions"]))
        if rv:
            st.subheader("Re-validation")
            st.write(f"Triggered {rv['triggered_at']} on month {rv['month']}. Report: "
                     f"`{rv.get('report_html')}` (overall {str(rv.get('overall_rag')).upper()}). "
                     f"Gate 4 now: **{stage_status(card, 'validate')}**. Previous validation: "
                     f"{rv.get('previous_validation')}")
            if stage_status(card, "validate") == "awaiting_review":
                st.info("Review it on the Gate review page.")
        ev_path = Path(AUDIT) / "monitoring_events.jsonl"
        if ev_path.exists():
            events = [json.loads(x) for x in ev_path.read_text().splitlines()]
            events = [e for e in events if e["model_id"] == model_id]
            if events:
                st.subheader("Monitoring events (append-only)")
                st.dataframe(pd.DataFrame(events), use_container_width=True, hide_index=True)
