import pytest

from factory import gates
from factory.model_card import now_iso


def test_record_and_status(tmp_path):
    assert gates.status(tmp_path, "m", "discover") == "pending"
    gates.record_decision(tmp_path, "m", "discover", "comment", "alice", "check owner")
    assert gates.status(tmp_path, "m", "discover") == "pending"
    gates.record_decision(tmp_path, "m", "discover", "approve", "alice")
    assert gates.status(tmp_path, "m", "discover") == "approved"
    log = gates.history(tmp_path, "m")
    assert [e["decision"] for e in log] == ["comment", "approve"]
    assert all(e["reviewer"] == "alice" and e["timestamp"] for e in log)


def test_reject_needs_reason_and_reviewer(tmp_path):
    with pytest.raises(gates.GateError):
        gates.record_decision(tmp_path, "m", "discover", "reject", "alice", "")
    with pytest.raises(gates.GateError):
        gates.record_decision(tmp_path, "m", "discover", "approve", "  ")
    with pytest.raises(gates.GateError):
        gates.record_decision(tmp_path, "m", "discover", "maybe", "alice")


def test_since_ignores_decisions_for_older_runs(tmp_path):
    gates.record_decision(tmp_path, "m", "document", "reject", "bob", "missing assumptions")
    rerun_finished = now_iso()
    assert gates.status(tmp_path, "m", "document") == "rejected"
    assert gates.status(tmp_path, "m", "document", since=rerun_finished) == "pending"
