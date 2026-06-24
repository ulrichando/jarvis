"""Phase 3 — the 50-cycle build-orchestrator soak.

Runs cycle.run_cycle 50 consecutive times, rotating through every hostile state
the live loop hits — empty queue / a normal build / a build that CRASHES mid-run
/ paused — and asserts the loop NEVER raises and NEVER leaks the cycle marker.

This is the bar-#5 soak: with the supervised fault boundary in place, a crashing
build is bulkheaded (the batch continues) and the cycle always returns a
well-formed summary with the marker released — 50 times, deterministically.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_VOICE_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_VOICE_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_VOICE_AGENT_ROOT))

SOAK_CYCLES = 50


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_AUTOMOD_SPAWN_LIVE", "0")
    return tmp_path


def test_build_cycle_soak_50_runs_crash_free_and_leak_free(home, monkeypatch):
    from pipeline.automod import cycle
    from pipeline.automod._state import (cycle_marker_path, queue_path,
                                         set_evolution_paused)

    monkeypatch.setattr(cycle.throttle, "remaining_today", lambda: 999)

    async def fake_build(intent_id):
        if "crash" in intent_id:
            raise RuntimeError(f"injected build crash: {intent_id}")
        return ({"id": intent_id, "status": "pending"}, True)

    monkeypatch.setattr(cycle, "_build", fake_build)

    qp = queue_path()
    qp.parent.mkdir(parents=True, exist_ok=True)

    failures: list[tuple] = []
    for i in range(SOAK_CYCLES):
        state = i % 4
        set_evolution_paused(False)
        if state == 0:                       # empty queue → clean no-op
            qp.write_text("")
        elif state == 1:                     # a normal build
            qp.write_text(json.dumps({"id": f"ok-{i}", "priority": "P2"}) + "\n")
        elif state == 2:                     # a CRASHING build first, then a good one
            qp.write_text(json.dumps({"id": f"crash-{i}", "priority": "P1"}) + "\n"
                          + json.dumps({"id": f"ok-{i}", "priority": "P2"}) + "\n")
        else:                                # paused → early return
            qp.write_text(json.dumps({"id": f"ok-{i}", "priority": "P2"}) + "\n")
            set_evolution_paused(True)

        try:
            summary = cycle.run_cycle(detect_first=False, assess_first=False)
        except BaseException as e:           # the loop must NEVER propagate
            failures.append((i, state, repr(e)))
            continue
        if not isinstance(summary, dict):
            failures.append((i, state, f"non-dict summary: {summary!r}"))
        if cycle_marker_path().exists():
            failures.append((i, state, "cycle marker LEAKED"))

    assert failures == [], f"{len(failures)}/{SOAK_CYCLES} cycles failed: {failures[:5]}"
