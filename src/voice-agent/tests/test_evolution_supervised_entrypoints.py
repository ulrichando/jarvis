"""Phase 3 — every autonomous entry point runs UNDER the fault boundary.

Proves the wiring (not just the boundary unit): a single bad build never aborts
cycle.run_cycle's batch (bulkhead), the cycle marker is always released, and
neither run_cycle nor the watchdog tick (the deploy safety net) ever propagates
an unhandled exception.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

_VOICE_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_VOICE_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_VOICE_AGENT_ROOT))


def _raise(msg: str):
    raise RuntimeError(msg)


def _iso_ago(seconds: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - seconds))


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_AUTOMOD_SPAWN_LIVE", "0")
    return tmp_path


# ── cycle.run_cycle — bulkhead: one bad build never aborts the batch ──────────

def test_run_cycle_one_bad_build_does_not_abort_batch(home, monkeypatch):
    from pipeline.automod import cycle
    from pipeline.automod._state import cycle_marker_path, queue_path

    qp = queue_path()
    qp.parent.mkdir(parents=True, exist_ok=True)
    qp.write_text(json.dumps({"id": "intent-A", "priority": "P1"}) + "\n"
                  + json.dumps({"id": "intent-B", "priority": "P2"}) + "\n")
    monkeypatch.setattr(cycle.throttle, "remaining_today", lambda: 999)

    built: list[str] = []

    async def fake_build(intent_id):
        if intent_id == "intent-A":
            raise RuntimeError("build A blew up")
        built.append(intent_id)
        return ({"id": intent_id, "status": "pending"}, True)

    monkeypatch.setattr(cycle, "_build", fake_build)

    summary = cycle.run_cycle(detect_first=False, assess_first=False)

    assert isinstance(summary, dict)
    assert not cycle_marker_path().exists()            # marker always released
    assert "intent-B" in built                         # batch continued past A's crash
    statuses = {o.get("id"): o.get("status") for o in summary.get("built", [])}
    assert statuses.get("intent-A") == "error"         # A's crash recorded, not lost


def test_run_cycle_never_propagates_unexpected_crash(home, monkeypatch):
    from pipeline.automod import cycle, spawner
    from pipeline.automod._state import cycle_marker_path

    # A crash OUTSIDE the build loop (queue read) → top-level guard must catch it.
    monkeypatch.setattr(spawner, "_read_queue", lambda: _raise("queue corrupt"))

    summary = cycle.run_cycle(detect_first=False, assess_first=False)

    assert isinstance(summary, dict)
    assert not cycle_marker_path().exists()


# ── watchdog.run_once — the safety net must never crash ──────────────────────

def test_watchdog_run_once_never_propagates(home, monkeypatch):
    from pipeline.automod import deploy, watchdog

    deploy.write_marker({
        "automod_id": "x", "rollback_sha": "s",
        "deployed_at": _iso_ago(600), "deadline_s": 300,
        "restart_requested_monotonic": 1.0,
    })
    # The health probe blows up mid-tick — today this kills the watchdog process.
    monkeypatch.setattr(watchdog, "_liveness", lambda: _raise("liveness probe crashed"))

    status = watchdog.run_once()      # must NOT raise

    assert status == "crashed"


# ── nightly.run / ondemand.run — uniform top-level guard ─────────────────────

def test_nightly_run_never_propagates(home, monkeypatch):
    from pipeline.automod import deploy, nightly

    # A crash in the unguarded guard-region (pre-checks run outside any try).
    monkeypatch.setattr(deploy, "read_marker", lambda: _raise("marker read crashed"))

    summary = nightly.run()           # must NOT raise

    assert isinstance(summary, dict)
    assert summary.get("crashed") is True


def test_ondemand_run_never_propagates(home, monkeypatch):
    from pipeline.automod import deploy, ondemand

    monkeypatch.setattr(deploy, "read_marker", lambda: _raise("marker read crashed"))

    summary = ondemand.run("some-intent")   # must NOT raise

    assert isinstance(summary, dict)
    assert summary.get("crashed") is True
