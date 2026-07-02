"""Throttle + governance gate — redesigned 2026-06-27 (cost budget + idle/cooldown;
count cap demoted to an emergency backstop)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_VOICE_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_VOICE_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_VOICE_AGENT_ROOT))


@pytest.fixture(autouse=True)
def _force_idle(tmp_path, monkeypatch):
    # Point the idle check (throttle._idle_seconds, via JARVIS_TELEMETRY_PATH —
    # the same var conftest uses) at a non-existent db so the gate reads "very
    # idle". Tests for the idle gate monkeypatch the helper directly.
    monkeypatch.setenv("JARVIS_TELEMETRY_PATH", str(tmp_path / "no-telemetry.db"))


def _intent(intent="fix X", kind="correction", **overrides):
    base = {"id": "test-001", "kind": kind, "intent": intent,
            "rationale": "test", "created_at": "2026-05-24T00:00:00Z"}
    base.update(overrides)
    return base


def test_admit_clean_intent(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    from pipeline.automod import throttle
    ok, reason = throttle.admit_intent(_intent())
    assert ok, reason


def test_default_daily_cap_is_five(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.delenv("JARVIS_AUTOMOD_DAILY_CAP", raising=False)
    from pipeline.automod import throttle
    assert throttle.daily_cap() == 5


def test_reject_empty_intent(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    from pipeline.automod import throttle
    ok, reason = throttle.admit_intent(_intent(intent=""))
    assert not ok
    assert "empty" in reason.lower()


def test_reject_whitespace_only_intent(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    from pipeline.automod import throttle
    ok, reason = throttle.admit_intent(_intent(intent="   \n  "))
    assert not ok


def test_reject_blocked_path(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    from pipeline.automod import throttle
    intent = _intent()
    intent["proposed_paths_hint"] = ["src/voice-agent/sanitizers/dsml.py"]
    ok, reason = throttle.admit_intent(intent)
    assert not ok
    assert "block" in reason.lower()


def test_reject_path_outside_allowed_prefix(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    from pipeline.automod import throttle
    intent = _intent()
    intent["proposed_paths_hint"] = ["src/voice-agent/desktop-tauri/src/App.jsx"]
    ok, reason = throttle.admit_intent(intent)
    assert not ok


def test_admit_with_clean_paths_hint(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    from pipeline.automod import throttle
    intent = _intent()
    intent["proposed_paths_hint"] = [
        "src/voice-agent/prompts/supervisor.md",
        "src/voice-agent/tools/memory.py",
    ]
    ok, reason = throttle.admit_intent(intent)
    assert ok, reason


# ── new governance gates ─────────────────────────────────────────────────────

def test_blocks_when_not_idle(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    from pipeline.automod import throttle
    monkeypatch.setattr(throttle, "_idle_seconds", lambda: 30)  # 30s < 10min
    ok, reason = throttle.admit_intent(_intent())
    assert not ok and reason == "not_idle"


def test_blocks_when_budget_exhausted(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    from pipeline.automod import throttle
    monkeypatch.setattr(throttle, "_budget_spent", lambda: 6.0)  # == default daily_usd
    ok, reason = throttle.admit_intent(_intent())
    assert not ok and reason == "budget_exhausted"


def test_blocks_on_cooldown(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    from pipeline.automod import throttle
    monkeypatch.setattr(throttle, "_since_last_build_min", lambda: 5)  # < 60
    ok, reason = throttle.admit_intent(_intent())
    assert not ok and reason == "cooldown"


def test_admits_when_idle_budget_cooldown_ok(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    from pipeline.automod import throttle
    ok, reason = throttle.admit_intent(_intent())
    assert ok and reason == ""


def test_mark_admitted_stamps_count_and_cooldown(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    from pipeline.automod import throttle
    throttle.mark_admitted("id0")
    state = json.loads((tmp_path / "auto-mods" / "throttle.json").read_text())
    assert state["admitted_today"] == 1
    assert "last_build_ts" in state
    # cooldown is now active → the next admit is blocked
    ok, reason = throttle.admit_intent(_intent())
    assert not ok and reason == "cooldown"


def test_count_backstop_only_when_set(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_AUTOMOD_DAILY_CAP", "2")
    from pipeline.automod import throttle
    monkeypatch.setattr(throttle, "_since_last_build_min", lambda: 1e9)  # bypass cooldown
    assert throttle.admit_intent(_intent(id="i1"))[0]
    throttle.mark_admitted("i1")
    assert throttle.admit_intent(_intent(id="i2"))[0]
    throttle.mark_admitted("i2")
    ok, reason = throttle.admit_intent(_intent(id="i3"))
    assert not ok and "cap" in reason.lower()


def test_reset_after_new_day(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_AUTOMOD_DAILY_CAP", "1")
    from pipeline.automod import throttle
    (tmp_path / "auto-mods").mkdir(parents=True, exist_ok=True)
    (tmp_path / "auto-mods" / "throttle.json").write_text(
        json.dumps({"date": "2026-05-22", "admitted_today": 1}))
    ok, reason = throttle.admit_intent(_intent())
    assert ok, reason
