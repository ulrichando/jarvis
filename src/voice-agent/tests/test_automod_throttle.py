"""Spec B (Plane 3) — throttle + blocklist gate on the intent queue."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_VOICE_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_VOICE_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_VOICE_AGENT_ROOT))


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


def test_reject_after_daily_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_AUTOMOD_DAILY_CAP", "2")
    from pipeline.automod import throttle
    assert throttle.admit_intent(_intent(**{"id": "i1"}))[0]
    throttle.mark_admitted("i1")
    assert throttle.admit_intent(_intent(**{"id": "i2"}))[0]
    throttle.mark_admitted("i2")
    ok, reason = throttle.admit_intent(_intent(**{"id": "i3"}))
    assert not ok
    assert "cap" in reason.lower()


def test_reset_after_new_day(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_AUTOMOD_DAILY_CAP", "1")
    from pipeline.automod import throttle

    # Fake "yesterday" by writing throttle.json with old date.
    state = {"date": "2026-05-22", "admitted_today": 1}
    (tmp_path / "auto-mods").mkdir(parents=True, exist_ok=True)
    (tmp_path / "auto-mods" / "throttle.json").write_text(json.dumps(state))
    ok, reason = throttle.admit_intent(_intent())
    assert ok, reason


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
    intent["proposed_paths_hint"] = ["src/desktop-tauri/src/App.jsx"]
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


def test_mark_admitted_persists_across_calls(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_AUTOMOD_DAILY_CAP", "5")
    from pipeline.automod import throttle
    for i in range(3):
        ok, _ = throttle.admit_intent(_intent(**{"id": f"id{i}"}))
        assert ok
        throttle.mark_admitted(f"id{i}")
    state = json.loads(
        (tmp_path / "auto-mods" / "throttle.json").read_text()
    )
    assert state["admitted_today"] == 3
