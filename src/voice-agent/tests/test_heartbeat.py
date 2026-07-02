"""Evolution loop heartbeat + gate-state derivation (2026-07-02).

compute_status() must name the right 'why it isn't building' state in gate order,
and beat()/read() must round-trip. Hermetic via JARVIS_HOME + monkeypatched
throttle helpers — no real telemetry / no live loop.
"""
from __future__ import annotations

import json

from pipeline.automod import heartbeat as hb


def _patch(monkeypatch, *, auto=True, paused=False, idle_s=9999, cooldown_min_left=0,
           spent=0.0, cap=6.0, cycle=False, deploy=False):
    from pipeline.automod import cost_ledger, throttle
    monkeypatch.setattr(hb, "is_auto_mode", lambda: auto)
    monkeypatch.setattr(hb, "is_evolution_paused", lambda: paused)
    monkeypatch.setattr(hb, "_cycle_running", lambda: cycle)
    monkeypatch.setattr(hb, "_active_deploy_present", lambda: deploy)
    monkeypatch.setattr(throttle, "_idle_seconds", lambda: idle_s)
    monkeypatch.setattr(throttle, "_idle_minutes", lambda: 10)
    monkeypatch.setattr(throttle, "_cooldown_minutes", lambda: 60)
    monkeypatch.setattr(throttle, "_since_last_build_min", lambda: 60 - cooldown_min_left)
    monkeypatch.setattr(cost_ledger, "spent_today", lambda: spent)
    monkeypatch.setattr(cost_ledger, "daily_usd", lambda: cap)


def test_manual_mode_state(monkeypatch):
    _patch(monkeypatch, auto=False)
    s = hb.compute_status()
    assert s["state"] == "manual"
    assert "not building" in s["reason"]


def test_paused_beats_everything(monkeypatch):
    _patch(monkeypatch, auto=True, paused=True, cycle=True)
    assert hb.compute_status()["state"] == "paused"


def test_deploying_state(monkeypatch):
    _patch(monkeypatch, deploy=True)
    assert hb.compute_status()["state"] == "deploying"


def test_building_state(monkeypatch):
    _patch(monkeypatch, cycle=True)
    assert hb.compute_status()["state"] == "building"


def test_waiting_when_user_active(monkeypatch):
    _patch(monkeypatch, idle_s=30)  # < 10min → user active
    s = hb.compute_status()
    assert s["state"] == "waiting"
    assert "quiet" in s["reason"]


def test_budget_state(monkeypatch):
    _patch(monkeypatch, idle_s=9999, spent=6.0, cap=6.0)
    assert hb.compute_status()["state"] == "budget"


def test_cooldown_state_reports_minutes(monkeypatch):
    _patch(monkeypatch, idle_s=9999, cooldown_min_left=34)
    s = hb.compute_status()
    assert s["state"] == "cooldown"
    assert "34m" in s["reason"]
    assert 33 * 60 <= s["cooldown_left_s"] <= 34 * 60 + 1


def test_ready_state_when_all_gates_clear(monkeypatch):
    _patch(monkeypatch, idle_s=9999, cooldown_min_left=0, spent=0.0)
    s = hb.compute_status()
    assert s["state"] == "ready"
    assert "will build" in s["reason"]


def test_gate_order_cooldown_only_after_idle_and_budget(monkeypatch):
    # user active AND cooldown pending → "waiting" wins (idle gate is earlier)
    _patch(monkeypatch, idle_s=5, cooldown_min_left=30)
    assert hb.compute_status()["state"] == "waiting"


def test_compute_status_never_raises(monkeypatch):
    def boom():
        raise RuntimeError("throttle exploded")
    from pipeline.automod import throttle
    monkeypatch.setattr(throttle, "_idle_seconds", boom)
    monkeypatch.setattr(hb, "is_auto_mode", lambda: True)
    monkeypatch.setattr(hb, "is_evolution_paused", lambda: False)
    s = hb.compute_status()
    assert s["state"] == "unknown"


def test_beat_and_read_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    _patch(monkeypatch, idle_s=9999)
    hb.beat()
    got = hb.read()
    assert got is not None
    assert got["state"] == "ready"
    assert "ts" in got
    # written where the API expects it
    assert json.loads((tmp_path / "auto-mods" / "heartbeat.json").read_text())["state"] == "ready"


def test_read_missing_is_none(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    assert hb.read() is None
