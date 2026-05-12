"""Tests for Producer A — per-turn correction-phrase capture."""
from __future__ import annotations

from pathlib import Path


def test_observe_emits_proposal_on_correction_phrase(tmp_path, monkeypatch):
    from pipeline.evolution import live_capture, audit_log

    log_path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(audit_log, "LOG_PATH", log_path)

    capture = live_capture.LiveCapture()
    capture.observe(turn_id="t-1000", user_text="that's fine", jarvis_text="ok")
    capture.observe(
        turn_id="t-1001",
        user_text="open chrome",
        jarvis_text="Launching Chromium…",
    )
    proposal = capture.observe(
        turn_id="t-1002",
        user_text="don't open chromium, I said chrome",
        jarvis_text="(silence)",
    )

    assert proposal is not None
    assert proposal["evidence_turns"] == ["t-1001", "t-1002"]
    assert "chromium" in proposal["evidence_quote"].lower()
    assert proposal["pattern"]


def test_observe_returns_none_when_no_correction(tmp_path, monkeypatch):
    from pipeline.evolution import live_capture, audit_log
    monkeypatch.setattr(audit_log, "LOG_PATH", tmp_path / "audit.jsonl")

    capture = live_capture.LiveCapture()
    out = capture.observe(turn_id="t-1", user_text="hello", jarvis_text="hi")
    assert out is None


def test_observe_dedups_consecutive_corrections_within_window(tmp_path, monkeypatch):
    from pipeline.evolution import live_capture, audit_log
    monkeypatch.setattr(audit_log, "LOG_PATH", tmp_path / "audit.jsonl")

    capture = live_capture.LiveCapture()
    capture.observe(turn_id="t-1", user_text="open chrome", jarvis_text="Chromium")
    first = capture.observe(
        turn_id="t-2", user_text="don't open chromium", jarvis_text="(silence)"
    )
    second = capture.observe(
        turn_id="t-3", user_text="don't open chromium", jarvis_text="(silence)"
    )

    assert first is not None
    assert second is None


def test_proposal_carries_rule_field_for_auto_stage(tmp_path, monkeypatch):
    """Regression: live_capture proposals MUST carry a 'rule' field
    because lifecycle.auto_stage reads proposal['rule']. Without this,
    Task 7.4's wireup catches a KeyError every time live_capture fires
    in production with JARVIS_EVOLUTION_LOGGING_ONLY=0.
    """
    from pipeline.evolution import live_capture, audit_log
    monkeypatch.setattr(audit_log, "LOG_PATH", tmp_path / "audit.jsonl")

    capture = live_capture.LiveCapture()
    capture.observe(
        turn_id="t-1",
        user_text="open chrome",
        jarvis_text="Launching Chromium…",
    )
    proposal = capture.observe(
        turn_id="t-2",
        user_text="don't open chromium",
        jarvis_text="(silence)",
    )

    assert proposal is not None
    assert "rule" in proposal
    assert isinstance(proposal["rule"], str)
    assert proposal["rule"].strip()
    assert "chromium" in proposal["rule"].lower() or "chromium" in proposal["prior_jarvis"].lower()
