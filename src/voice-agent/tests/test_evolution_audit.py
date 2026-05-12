"""Tests for the append-only evolution audit log."""
from __future__ import annotations

import json
from pathlib import Path


def test_append_event_writes_jsonl_record(tmp_path, monkeypatch):
    from pipeline.evolution import audit_log

    target = tmp_path / "evolution_log.jsonl"
    monkeypatch.setattr(audit_log, "LOG_PATH", target)

    audit_log.append_event(
        rule_id="R-0021",
        kind="tier_transition",
        from_tier="proposed",
        to_tier="staged",
        reason="evaluator pass 5/5",
        evidence_turns=["t-2301"],
        evaluator_scores={"replay": "0/0", "redteam": "0/10", "poll": "3/3"},
    )

    lines = target.read_text().strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["rule_id"] == "R-0021"
    assert record["kind"] == "tier_transition"
    assert record["from_tier"] == "proposed"
    assert record["to_tier"] == "staged"
    assert record["evidence_turns"] == ["t-2301"]
    assert "ts" in record


def test_append_event_is_append_only(tmp_path, monkeypatch):
    from pipeline.evolution import audit_log

    target = tmp_path / "evolution_log.jsonl"
    monkeypatch.setattr(audit_log, "LOG_PATH", target)

    audit_log.append_event(rule_id="R-1", kind="proposal", reason="first")
    audit_log.append_event(rule_id="R-2", kind="proposal", reason="second")
    audit_log.append_event(rule_id="R-3", kind="proposal", reason="third")

    lines = target.read_text().strip().splitlines()
    assert len(lines) == 3
    assert json.loads(lines[0])["rule_id"] == "R-1"
    assert json.loads(lines[2])["rule_id"] == "R-3"


def test_append_event_swallows_io_errors(tmp_path, monkeypatch):
    from pipeline.evolution import audit_log

    bad_path = tmp_path / "does" / "not" / "exist" / "log.jsonl"
    monkeypatch.setattr(audit_log, "LOG_PATH", bad_path)
    monkeypatch.setattr(audit_log, "_ALLOW_MKDIR", False)

    audit_log.append_event(rule_id="R-1", kind="test", reason="should not crash")
