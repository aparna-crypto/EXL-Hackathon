import mlflow
import pytest

from factory import gates, model_card, orchestrator


@pytest.fixture
def models_dir(tmp_path):
    d = tmp_path / "models" / "toy_model"
    d.mkdir(parents=True)
    (d / "model.R").write_text("fit <- glm(y ~ x, family = binomial())\n")
    return tmp_path / "models"


def counting_stages(cfg, counts):
    def make(stage):
        def fn(card, ctx):
            counts[stage] = counts.get(stage, 0) + 1
            return {"summary": f"{stage} done"}
        return fn
    return {s: make(s) for s in cfg["gates"]["stages"]}


def approve_all(card, stage, summary):
    return "approve", "tester", "ok"


def test_full_run_with_approvals(cfg, models_dir):
    counts = {}
    out = orchestrator.run("toy_model", cfg, counting_stages(cfg, counts), approve_all,
                           models_dir, use_mlflow=False)
    assert out["status"] == "complete"
    card = model_card.load(cfg["paths"]["model_cards"], "toy_model")
    assert card["language"] == "R"
    assert all(e["status"] == "approved" for e in card["stages"].values())
    assert len(gates.history(cfg["paths"]["audit"], "toy_model")) == 4
    # re-running an approved pipeline does nothing
    out = orchestrator.run("toy_model", cfg, counting_stages(cfg, counts), approve_all,
                           models_dir, use_mlflow=False)
    assert out["status"] == "complete" and set(counts.values()) == {1}


def test_reject_stops_and_rerun_redoes_stage(cfg, models_dir):
    counts = {}
    stages = counting_stages(cfg, counts)
    answers = iter([("approve", "t", ""), ("comment", "t", "looks thin"),
                    ("reject", "t", "missing assumptions")])
    out = orchestrator.run("toy_model", cfg, stages, lambda *a: next(answers), models_dir,
                           use_mlflow=False)
    assert out == {"status": "rejected", "stage": "document", "message": out["message"]}
    out = orchestrator.run("toy_model", cfg, stages, approve_all, models_dir, use_mlflow=False)
    assert out["status"] == "complete"
    assert counts == {"discover": 1, "document": 2, "redevelop": 1, "validate": 1}
    decisions = [e["decision"] for e in gates.history(cfg["paths"]["audit"], "toy_model",
                                                      "document")]
    assert decisions == ["comment", "reject", "approve"]


def test_defer_to_ui_then_resume(cfg, models_dir):
    counts = {}
    stages = counting_stages(cfg, counts)
    out = orchestrator.run("toy_model", cfg, stages, None, models_dir, use_mlflow=False)
    assert out["status"] == "awaiting_review" and out["stage"] == "discover"
    card = model_card.load(cfg["paths"]["model_cards"], "toy_model")
    gates.record_decision(cfg["paths"]["audit"], "toy_model", "discover", "approve", "ui-user",
                          card_version=card["card_version"])
    out = orchestrator.run("toy_model", cfg, stages, None, models_dir, use_mlflow=False)
    assert out["status"] == "awaiting_review" and out["stage"] == "document"
    assert counts == {"discover": 1, "document": 1}


def test_not_applicable_stage_is_skipped(cfg, models_dir):
    stages = counting_stages(cfg, {})
    stages["redevelop"] = lambda card, ctx: {"summary": "only R scorecard", "applicable": False}
    out = orchestrator.run("toy_model", cfg, stages, approve_all, models_dir, use_mlflow=False)
    assert out["status"] == "complete"
    card = model_card.load(cfg["paths"]["model_cards"], "toy_model")
    assert card["stages"]["redevelop"]["status"] == "not_applicable"


def test_unimplemented_stage_and_unknown_model(cfg, models_dir):
    out = orchestrator.run("toy_model", cfg, {}, approve_all, models_dir, use_mlflow=False)
    assert out["status"] == "not_implemented" and out["stage"] == "discover"
    with pytest.raises(FileNotFoundError):
        orchestrator.run("nope", cfg, {}, approve_all, models_dir, use_mlflow=False)


def test_pipeline_and_gates_logged_to_mlflow(cfg, models_dir):
    orchestrator.run("toy_model", cfg, counting_stages(cfg, {}), approve_all, models_dir)
    runs = mlflow.search_runs(experiment_names=[cfg["tracking"]["experiment"]])
    kinds = sorted(runs["tags.kind"])
    assert kinds == ["gate_decision"] * 4 + ["pipeline"]
