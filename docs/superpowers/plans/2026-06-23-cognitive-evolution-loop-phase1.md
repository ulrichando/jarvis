# Cognitive Evolution Loop — Phase 1 (Event-Driven Trigger) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the blind 30-minute evolution poll with an experience-driven trigger — evolution wakes when a real signal occurs (a bug/error, a correction, a new fact learned), not on a clock — while keeping the manual-mode build guard (manual = queue only).

**Architecture:** A tiny thread-safe signal module (`pipeline/automod/signal.py`) exposes `bump(reason)` / `await wait(timeout)`. Producers (the telemetry turn-writer, the memory tool, the error logger) call `bump()` when something evolution-worthy happens. The existing in-process `_automod_loop` in `jarvis_agent.py` waits on that signal (with a slow backstop timeout) instead of `asyncio.sleep(1800)`, then scans + (auto-mode only) builds. Cross-thread-safe via `threading.Event` + `asyncio.to_thread`, so producers on any thread can wake the loop without an event-loop reference.

**Tech Stack:** Python 3.13, asyncio, `threading.Event`, sqlite3 (existing telemetry), pytest (+ `asyncio` via the existing pattern in `tests/test_automod_spawner.py`).

**Prerequisite (handled separately, NOT a task here):** committed `master` currently has 1 red test (`test_automod_patterns.py::test_confab_self_flag_emits_at_threshold`). Builds correctly refuse to commit on a red baseline, so *end-to-end* build verification (a green proposal reaching Review) is blocked until that test is greened. Phase 1's own tests do not depend on it. Green master before exercising the auto-mode build path live.

**Phase 0 (DONE, committed c2c6abca):** `_automod_loop` now gates `drain_queue` on `is_auto_mode()`. This plan replaces the loop's *timing* (poll → event) and keeps that gate.

---

### Task 1: Thread-safe signal module

**Files:**
- Create: `src/voice-agent/pipeline/automod/signal.py`
- Test: `src/voice-agent/tests/test_automod_signal.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_automod_signal.py
import asyncio
import time
import pytest
from pipeline.automod import signal


def test_bump_records_reasons_and_sets_event():
    signal.clear()
    signal.drain_reasons()  # reset
    signal.bump("error:tool_x")
    signal.bump("correction:stop saying sir")
    assert signal.is_set() is True
    reasons = signal.drain_reasons()
    assert reasons == ["error:tool_x", "correction:stop saying sir"]
    # drain_reasons leaves the buffer empty
    assert signal.drain_reasons() == []


@pytest.mark.asyncio
async def test_wait_returns_true_when_bumped():
    signal.clear()
    loop = asyncio.get_running_loop()
    loop.call_later(0.05, signal.bump, "fact:user birthday")
    bumped = await signal.wait(timeout=2.0)
    assert bumped is True


@pytest.mark.asyncio
async def test_wait_returns_false_on_timeout():
    signal.clear()
    signal.drain_reasons()
    bumped = await signal.wait(timeout=0.1)
    assert bumped is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_automod_signal.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'pipeline.automod.signal'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/voice-agent/pipeline/automod/signal.py
"""Process-global experience signal for the cognitive evolution loop.

Producers (telemetry turn-writer, memory tool, error logger) call bump() when
something evolution-worthy happens — a bug, a correction, a new fact. The
in-process _automod_loop awaits wait(), so evolution reacts to lived experience
instead of a fixed clock.

Cross-thread-safe: bump() may be called from any thread (telemetry/tool code
need not be on the agent's event loop). wait() blocks off-loop via
asyncio.to_thread on a threading.Event, so no event-loop reference is needed.

Lives under pipeline/automod/ (auto-mod HARD BLOCKLIST) — human-edited only.
"""
from __future__ import annotations

import asyncio
import collections
import threading

_event = threading.Event()
_reasons: collections.deque[str] = collections.deque(maxlen=50)
_lock = threading.Lock()


def bump(reason: str) -> None:
    """Record a reason + wake the loop. Safe from any thread. Never raises."""
    try:
        with _lock:
            _reasons.append(str(reason))
        _event.set()
    except Exception:
        pass


def drain_reasons() -> list[str]:
    """Return + clear the recorded reasons (consumed by the loop / reflection)."""
    with _lock:
        out = list(_reasons)
        _reasons.clear()
    return out


def is_set() -> bool:
    return _event.is_set()


def clear() -> None:
    _event.clear()


async def wait(timeout: float) -> bool:
    """Block off the event loop until bumped or `timeout` seconds elapse.
    Returns True if bumped, False on timeout."""
    return await asyncio.to_thread(_event.wait, timeout)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_automod_signal.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/pipeline/automod/signal.py src/voice-agent/tests/test_automod_signal.py
git commit -m "feat(evolution): thread-safe experience signal for event-driven trigger"
```

---

### Task 2: Bump the signal from the telemetry turn-writer

**Files:**
- Modify: `src/voice-agent/pipeline/turn_telemetry.py` (inside `log_turn`, after the row is written)
- Test: `src/voice-agent/tests/test_automod_signal_telemetry.py`

A turn carries an evolution-worthy signal when it has a `correction_signal` (user corrected JARVIS) or a bad `confab_check_state` (`hedged_no_evidence` / `retry_factory_missing`). `log_turn` already receives both as params.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_automod_signal_telemetry.py
from pipeline.automod import signal
from pipeline import turn_telemetry


def test_correction_turn_bumps_signal(monkeypatch):
    bumped = []
    monkeypatch.setattr(signal, "bump", lambda reason: bumped.append(reason))
    turn_telemetry._maybe_signal_evolution(
        correction_signal="stop saying sir", confab_check_state=None
    )
    assert bumped and bumped[0].startswith("correction:")


def test_confab_turn_bumps_signal(monkeypatch):
    bumped = []
    monkeypatch.setattr(signal, "bump", lambda reason: bumped.append(reason))
    turn_telemetry._maybe_signal_evolution(
        correction_signal=None, confab_check_state="hedged_no_evidence"
    )
    assert bumped and bumped[0].startswith("confab:")


def test_clean_turn_does_not_bump(monkeypatch):
    bumped = []
    monkeypatch.setattr(signal, "bump", lambda reason: bumped.append(reason))
    turn_telemetry._maybe_signal_evolution(
        correction_signal=None, confab_check_state="ok"
    )
    assert bumped == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_automod_signal_telemetry.py -q`
Expected: FAIL — `AttributeError: module 'pipeline.turn_telemetry' has no attribute '_maybe_signal_evolution'`

- [ ] **Step 3: Write minimal implementation**

Add this helper near the top of `pipeline/turn_telemetry.py` (after imports):

```python
# Bad confab states that mean JARVIS claimed/hedged without evidence — an
# evolution-worthy signal (mirrors introspection.gather_evidence's query).
_CONFAB_BAD_STATES = {"hedged_no_evidence", "retry_factory_missing"}


def _maybe_signal_evolution(correction_signal, confab_check_state) -> None:
    """Wake the cognitive evolution loop when a turn carried a bug/correction.
    Best-effort; never raises into the telemetry write."""
    try:
        reason = None
        if correction_signal:
            reason = f"correction:{str(correction_signal)[:80]}"
        elif confab_check_state in _CONFAB_BAD_STATES:
            reason = f"confab:{confab_check_state}"
        if reason:
            from pipeline.automod import signal as _signal
            _signal.bump(reason)
    except Exception:
        pass
```

Then, inside `log_turn`, immediately AFTER the row insert/commit succeeds (search for where the `INSERT` is committed — the function ends after writing the row), add:

```python
    _maybe_signal_evolution(correction_signal, confab_check_state)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_automod_signal_telemetry.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Run the telemetry suite to confirm no regression**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_turn_telemetry.py -q`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add src/voice-agent/pipeline/turn_telemetry.py src/voice-agent/tests/test_automod_signal_telemetry.py
git commit -m "feat(evolution): bump experience signal on correction/confab turns"
```

---

### Task 3: Bump the signal when JARVIS learns a new fact (memory write)

**Files:**
- Modify: `src/voice-agent/tools/memory.py` (in the add/replace write path)
- Test: `src/voice-agent/tests/test_automod_signal_memory.py`

- [ ] **Step 1: Read the write path**

Run: `cd src/voice-agent && grep -nE "def _handle_memory|action|add|replace" tools/memory.py | head -20`
Note the exact place where an `add`/`replace` action has succeeded (a new fact persisted).

- [ ] **Step 2: Write the failing test**

```python
# tests/test_automod_signal_memory.py
from pipeline.automod import signal
from tools import memory


def test_memory_add_bumps_signal(monkeypatch, tmp_path):
    bumped = []
    monkeypatch.setattr(signal, "bump", lambda reason: bumped.append(reason))
    # _signal_new_fact is the seam Task 3 adds; it bumps for add/replace only.
    memory._signal_new_fact("add")
    assert bumped and bumped[0].startswith("fact:")


def test_memory_read_does_not_bump(monkeypatch):
    bumped = []
    monkeypatch.setattr(signal, "bump", lambda reason: bumped.append(reason))
    memory._signal_new_fact("read")
    assert bumped == []
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_automod_signal_memory.py -q`
Expected: FAIL — `AttributeError: module 'tools.memory' has no attribute '_signal_new_fact'`

- [ ] **Step 4: Write minimal implementation**

Add to `tools/memory.py`:

```python
def _signal_new_fact(action: str) -> None:
    """Wake the cognitive evolution loop when JARVIS learns a new fact.
    Only add/replace count (read/remove are not 'learning'). Never raises."""
    try:
        if str(action) in ("add", "replace"):
            from pipeline.automod import signal as _signal
            _signal.bump(f"fact:memory_{action}")
    except Exception:
        pass
```

Then call `_signal_new_fact(action)` right after a successful `add`/`replace` write in `_handle_memory` (use the action variable already in scope).

- [ ] **Step 5: Run test to verify it passes**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_automod_signal_memory.py -q`
Expected: PASS (2 passed)

- [ ] **Step 6: Run the memory suite**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_file_memory.py -q`
Expected: PASS (all)

- [ ] **Step 7: Commit**

```bash
git add src/voice-agent/tools/memory.py src/voice-agent/tests/test_automod_signal_memory.py
git commit -m "feat(evolution): bump experience signal when a new fact is learned"
```

---

### Task 4: Make `_automod_loop` event-driven (wait on the signal, keep the mode gate)

**Files:**
- Modify: `src/voice-agent/jarvis_agent.py` (the `_automod_loop` defined under the `JARVIS_AUTOMOD_ENABLED` block, ~line 7384)
- Test: `src/voice-agent/tests/test_automod_tick.py`

Extract the loop *body* into a module-level coroutine so it's testable without an infinite loop, then have the loop `await signal.wait(backstop)` instead of `asyncio.sleep`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_automod_tick.py
import asyncio
import pytest
from pipeline.automod import _state


@pytest.mark.asyncio
async def test_tick_scans_but_does_not_build_in_manual(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))      # manual: no .evolution-auto
    calls = {"scan": 0, "drain": 0}
    import pipeline.automod.patterns as patterns
    import pipeline.automod.spawner as spawner
    monkeypatch.setattr(patterns, "scan_and_emit", lambda: calls.__setitem__("scan", calls["scan"] + 1))
    async def fake_drain(**kw):
        calls["drain"] += 1
        return 0
    monkeypatch.setattr(spawner, "drain_queue", fake_drain)

    from jarvis_agent import _automod_tick   # extracted in Step 3
    await _automod_tick()
    assert calls["scan"] == 1   # always scans (queues for review)
    assert calls["drain"] == 0  # manual mode never builds


@pytest.mark.asyncio
async def test_tick_builds_in_auto(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    _state.set_auto_mode(True)                            # auto: .evolution-auto present
    calls = {"scan": 0, "drain": 0}
    import pipeline.automod.patterns as patterns
    import pipeline.automod.spawner as spawner
    monkeypatch.setattr(patterns, "scan_and_emit", lambda: calls.__setitem__("scan", calls["scan"] + 1))
    async def fake_drain(**kw):
        calls["drain"] += 1
        return 0
    monkeypatch.setattr(spawner, "drain_queue", fake_drain)

    from jarvis_agent import _automod_tick
    await _automod_tick()
    assert calls["scan"] == 1
    assert calls["drain"] == 1  # auto mode builds
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_automod_tick.py -q`
Expected: FAIL — `ImportError: cannot import name '_automod_tick' from 'jarvis_agent'`

- [ ] **Step 3: Write minimal implementation**

At module scope in `jarvis_agent.py` (near the other module-level helpers, NOT nested in the session function), add:

```python
async def _automod_tick() -> None:
    """One evolution pass: always scan (queue for review); build only in AUTO
    mode. Extracted so it's unit-testable. 2026-06-23 cognitive-loop Phase 1."""
    from pipeline.automod import patterns as _automod_patterns
    from pipeline.automod import spawner as _automod_spawner
    from pipeline.automod._state import is_auto_mode
    _automod_patterns.scan_and_emit()
    if is_auto_mode():
        await _automod_spawner.drain_queue()
```

Then replace the existing nested `_automod_loop` body (the `while True:` block from Phase 0) with the event-driven version:

```python
            async def _automod_loop():
                from pipeline.automod import signal as _signal
                # Backstop: even with no signal, sweep at most this often so a
                # missed bump can't stall evolution forever. Default 2h.
                backstop = float(os.environ.get("JARVIS_AUTOMOD_BACKSTOP_S", "7200"))
                cooldown = float(os.environ.get("JARVIS_AUTOMOD_COOLDOWN_S", "30"))
                while True:
                    await _signal.wait(backstop)   # wakes on a real signal OR backstop
                    _signal.clear()
                    try:
                        await _automod_tick()
                    except Exception as _e:  # noqa: BLE001
                        logger.warning("[automod] tick failed: %s", _e)
                    # Debounce a burst of signals into one pass.
                    await asyncio.sleep(cooldown)
```

Update the adjacent log line to reflect the new model:

```python
            logger.info(
                "[automod] event-driven pattern detector + spawner scheduled "
                "(backstop=%ss; spawn_live=%s; mode-gated build)",
                os.environ.get("JARVIS_AUTOMOD_BACKSTOP_S", "7200"),
                os.environ.get("JARVIS_AUTOMOD_SPAWN_LIVE", "0"),
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_automod_tick.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Syntax-check the large file**

Run: `cd src/voice-agent && .venv/bin/python -c "import ast; ast.parse(open('jarvis_agent.py').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add src/voice-agent/jarvis_agent.py src/voice-agent/tests/test_automod_tick.py
git commit -m "feat(evolution): event-driven _automod_loop (waits on experience signal, mode-gated build)"
```

---

### Task 5: Full-suite verification + service restart

- [ ] **Step 1: Run the full voice-agent suite**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/ -q`
Expected: PASS (the only known red is the green-master prerequisite test, fixed separately; everything else green). If `test_hooks.py::test_fire_runs_script_with_payload_on_stdin` flakes, re-run it in isolation to confirm.

- [ ] **Step 2: Restart the voice-agent so the new loop is live (respect the active-session guard)**

Run: `sqlite3 ~/.local/share/jarvis/turn_telemetry.db "SELECT (strftime('%s','now') - strftime('%s', ts_utc)) FROM turns ORDER BY id DESC LIMIT 1"`
If the result is > 60 (no turn in the last minute): `systemctl --user restart jarvis-voice-agent.service`
Otherwise, ask the user first (CLAUDE.md operational rule).

- [ ] **Step 3: Confirm the event-driven scheduler logged its new line**

Run: `tail -50 ~/.local/share/jarvis/logs/voice-agent.log | grep "event-driven pattern detector"`
Expected: one line confirming `backstop=...` + `mode-gated build`.

---

## Follow-on plans (write after Phase 1 lands + verifies)

- **Phase 2 — experience-grounded reflection:** add a `recent_signals` param to `introspection.gather_evidence()` fed from `signal.drain_reasons()`, so `run_self_assessment` reasons about *what just happened* (the triggering bug/correction/fact), not only aggregate telemetry. One file (`introspection.py`) + tests.
- **Phase 3 — durable lessons memory:** new `pipeline/automod/lessons.py` (honcho-backed, jsonl fallback); `patterns.build_retry_intent` also appends a lesson; `introspection` reads lessons so a known-failed approach is excluded from new intents. Makes JARVIS learn from mistakes across sessions.

## Self-review notes
- Spec coverage: Phase 1 of the spec (event-driven trigger + mode gate) is fully covered by Tasks 1–4; Phases 2–3 are explicitly deferred to follow-on plans (spec says they're separable). Phase 0 is already committed.
- Types/seams consistent across tasks: `signal.bump(reason: str)`, `signal.wait(timeout: float) -> bool`, `signal.drain_reasons() -> list[str]`, `_automod_tick()` used identically in Task 4's test and impl.
- Prerequisite (green master) called out in the header and Task 5 Step 1 — not silently assumed.
