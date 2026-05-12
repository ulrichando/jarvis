"""Tests for the daily evolution report writer."""
from __future__ import annotations

import json
from pathlib import Path


def test_report_summarizes_24h_transitions(tmp_path, monkeypatch):
    from pipeline.evolution import report, audit_log

    log = tmp_path / "evolution_log.jsonl"
    monkeypatch.setattr(audit_log, "LOG_PATH", log)

    events = [
        {"ts": "2026-05-12T00:00:00Z", "kind": "tier_transition",
         "rule_id": "R-0001", "from_tier": "proposed", "to_tier": "staged",
         "reason": "evaluator pass"},
        {"ts": "2026-05-12T02:00:00Z", "kind": "tier_transition",
         "rule_id": "R-0002", "from_tier": "staged", "to_tier": "accepted",
         "reason": "7d shadow + golden pass"},
        {"ts": "2026-05-12T03:00:00Z", "kind": "tier_transition",
         "rule_id": "R-0003", "from_tier": "accepted", "to_tier": "archived",
         "reason": "duplicate"},
        {"ts": "2026-05-11T05:00:00Z", "kind": "tier_transition",
         "rule_id": "R-0900", "from_tier": "proposed", "to_tier": "staged",
         "reason": "old event — outside window"},
    ]
    log.write_text("\n".join(json.dumps(e) for e in events) + "\n")

    out = tmp_path / "evolution_report.md"
    monkeypatch.setattr(report, "REPORT_PATH", out)

    report.write_daily(window_start="2026-05-12T00:00:00Z")

    text = out.read_text()
    assert "1 staged" in text
    assert "1 promoted to accepted" in text or "promoted to accepted: 1" in text
    assert "1 archived" in text or "archived: 1" in text
    assert "R-0001" in text
    assert "R-0900" not in text


def test_report_handles_missing_audit_log(tmp_path, monkeypatch):
    from pipeline.evolution import report, audit_log

    monkeypatch.setattr(audit_log, "LOG_PATH", tmp_path / "missing.jsonl")
    out = tmp_path / "evolution_report.md"
    monkeypatch.setattr(report, "REPORT_PATH", out)

    report.write_daily(window_start="2026-05-12T00:00:00Z")

    assert out.exists()
    assert "No evolution activity" in out.read_text() or "0 staged" in out.read_text()
