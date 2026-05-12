"""Tests for Producer C — 24 h contradiction / staleness detector."""
from __future__ import annotations

from pipeline.evolution.schema import Rule


def test_detects_near_duplicates():
    from pipeline.evolution.contradiction_detector import find_duplicates

    rules = [
        Rule(id="R-1", tier="accepted",
             text='When the user says "Chrome", launch /usr/bin/google-chrome.'),
        Rule(id="R-2", tier="accepted",
             text='When the user says "Google Chrome", launch /usr/bin/google-chrome.'),
        Rule(id="R-3", tier="accepted", text="Reply 'Yes?' to bare Jarvis pings."),
    ]

    dups = find_duplicates(rules, threshold=0.7)
    pairs = {(min(a, b), max(a, b)) for a, b in dups}
    assert ("R-1", "R-2") in pairs


def test_detects_dead_subsystem_refs():
    from pipeline.evolution.contradiction_detector import find_dead_subsystem_rules

    rules = [
        Rule(id="R-1", tier="accepted", text="Add ElevenLabs as TTS backup."),
        Rule(id="R-2", tier="accepted",
             text="Always answer 'Yes, sir?' to Jarvis pings."),
        Rule(id="R-3", tier="accepted",
             text="Use --profile-directory=Default with Chrome."),
    ]

    flagged = find_dead_subsystem_rules(rules)
    ids = {r.id for r in flagged}
    assert "R-1" in ids
    assert "R-2" in ids
    assert "R-3" not in ids


def test_run_detector_emits_archival_proposals(tmp_path, monkeypatch):
    from pipeline.evolution import contradiction_detector, audit_log
    monkeypatch.setattr(audit_log, "LOG_PATH", tmp_path / "audit.jsonl")

    rules = [
        Rule(id="R-1", tier="accepted",
             text="When user says Chrome launch /usr/bin/google-chrome."),
        Rule(id="R-2", tier="accepted",
             text="When user says Google Chrome launch /usr/bin/google-chrome."),
        Rule(id="R-3", tier="accepted", text="Add ElevenLabs as TTS backup."),
    ]
    proposals = contradiction_detector.run(rules)
    kinds = [p["kind"] for p in proposals]
    assert "archive_duplicate" in kinds
    assert "archive_dead_subsystem" in kinds
