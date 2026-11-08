import json

import yaml

from factory import benchmark, model_card


def _ledger(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_llm_usage_maps_agents_to_stages_and_skips_cached(tmp_path):
    base = {"input_tokens": 10, "output_tokens": 5, "cost_usd": 0.01, "duration_s": 2.0}
    _ledger(tmp_path / "l.jsonl", [
        {**base, "model_id": "m", "agent": "inventory", "cached": False},
        {**base, "model_id": "m", "agent": "inventory", "cached": True},
        {**base, "model_id": "m", "agent": "translation", "cached": False},
        {**base, "model_id": None, "agent": "healthcheck", "cached": False}])
    u = benchmark.llm_usage(tmp_path / "l.jsonl")
    assert set(u) == {"m"}
    assert u["m"]["discover"] == {"calls": 1, "live_s": 2.0, "tokens": 15, "cost_usd": 0.01}
    assert u["m"]["redevelop"]["calls"] == 1


def _row(machine_s=36.0, decisions=1):
    st = {s: {"ran": False, "machine_s": 0.0, "gate_decisions": 0} for s in benchmark.STAGES}
    st["validate"] = {"ran": True, "machine_s": machine_s, "gate_decisions": decisions}
    return {"model_id": "m", "stages": st}


def test_no_manual_baseline_means_no_speedup():
    c = benchmark.compare([_row()], {"activities": {"validate": None}})
    assert c[0]["manual_h"] is None and c[0]["time_saved_pct"] is None


def test_speedup_uses_supplied_baseline_and_review_time():
    base = {"review_minutes_per_gate": 30, "activities": {"validate": 10},
            "models": {"m": {"validate": 20}}}
    c = benchmark.compare([_row(machine_s=3600.0, decisions=2)], base)[0]
    assert c["manual_h"] == 20.0                        # per-model override wins
    assert c["review_h"] == 1.0 and c["automated_h"] == 2.0
    assert c["time_saved_pct"] == 90.0


def test_committed_baseline_file_is_empty_template():
    """The manual baseline is user-supplied: the repo must not ship invented figures."""
    from factory.config import REPO_ROOT, load_config
    b = yaml.safe_load((REPO_ROOT / load_config()["benchmark"]["manual_baseline"]).read_text())
    if b.get("source") is None:
        assert all(v is None for v in b["activities"].values()) and not b.get("models")


def test_benchmark_end_to_end_without_baseline(cfg, tmp_path):
    from factory.config import REPO_ROOT
    card = model_card.load(REPO_ROOT / "artifacts" / "model_cards", "sas_behaviour_scorecard")
    model_card.save(card, cfg["paths"]["model_cards"], "benchmark test")
    cfg["benchmark"]["manual_baseline"] = str(tmp_path / "missing.yaml")
    res = benchmark.run(cfg)
    r = res["models"][0]
    assert r["model_id"] == "sas_behaviour_scorecard" and r["stages"]["discover"]["ran"]
    assert not r["stages"]["validate"]["ran"]
    assert res["consistency"][0]["variable_coverage"].startswith("n/a")
    md = (tmp_path / "benchmark.md").read_text()
    assert "Manual baseline not supplied" in md
    assert (tmp_path / "benchmark" / "machine_time.png").exists()
