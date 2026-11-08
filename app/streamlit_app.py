"""Streamlit UI: inventory, gate approvals, audit log, LLM spend.

Run with `make ui`. Report viewer (Stage 5) and drift dashboard (Stage 6) are added later.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

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

else:
    st.info("Added in a later stage (Validation reports: Stage 5, Drift monitoring: Stage 6).")
