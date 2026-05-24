"""Track 2.5 — successful-trajectory gate for procedure capture."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_VOICE_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_VOICE_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_VOICE_AGENT_ROOT))


def _make_snap(**overrides):
    from pipeline.skill_review import TurnSnapshot
    base = dict(
        turn_id=1, ts_utc="2026-05-24T00:00:00Z",
        user_text="Jarvis, deploy the app",
        jarvis_text="Tests passed, pushed, CI green — deployed.",
        route="TASK_OTHER", subagent="", computer_use_steps=0,
        tool_call_count=3, had_tool_error=False,
    )
    base.update(overrides)
    return TurnSnapshot(**base)


def test_gate_passes_on_happy_path():
    from pipeline.skill_review import _is_successful_trajectory
    snap = _make_snap()
    assert _is_successful_trajectory(snap, wall_clock_s=15.0, user_followup_30s=0)


def test_gate_rejects_too_few_tools():
    from pipeline.skill_review import _is_successful_trajectory
    snap = _make_snap(tool_call_count=1)
    assert not _is_successful_trajectory(snap, wall_clock_s=15.0, user_followup_30s=0)


def test_gate_rejects_tool_error():
    from pipeline.skill_review import _is_successful_trajectory
    snap = _make_snap(had_tool_error=True)
    assert not _is_successful_trajectory(snap, wall_clock_s=15.0, user_followup_30s=0)


def test_gate_rejects_short_wall_clock():
    from pipeline.skill_review import _is_successful_trajectory
    snap = _make_snap()
    assert not _is_successful_trajectory(snap, wall_clock_s=3.0, user_followup_30s=0)


def test_gate_rejects_no_completion_claim():
    from pipeline.skill_review import _is_successful_trajectory
    snap = _make_snap(jarvis_text="I'm working on it...")
    assert not _is_successful_trajectory(snap, wall_clock_s=15.0, user_followup_30s=0)


def test_gate_rejects_no_intent_verb():
    from pipeline.skill_review import _is_successful_trajectory
    snap = _make_snap(user_text="what's the weather")
    assert not _is_successful_trajectory(snap, wall_clock_s=15.0, user_followup_30s=0)


def test_gate_rejects_user_correction_followup():
    from pipeline.skill_review import _is_successful_trajectory
    snap = _make_snap()
    # user_followup_30s=1 means there WAS a followup — risky to capture
    assert not _is_successful_trajectory(snap, wall_clock_s=15.0, user_followup_30s=1)


def test_gate_rejects_wrong_route():
    from pipeline.skill_review import _is_successful_trajectory
    snap = _make_snap(route="BANTER")
    assert not _is_successful_trajectory(snap, wall_clock_s=15.0, user_followup_30s=0)
