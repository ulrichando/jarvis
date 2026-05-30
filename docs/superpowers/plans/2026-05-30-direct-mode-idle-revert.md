# Direct-mode idle auto-revert — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a direct voice mode (Gemini/OpenAI) is idle for `JARVIS_DIRECT_IDLE_TIMEOUT_S` (default 300 s), auto-revert to JARVIS-Claude so provider quota stops burning while the user isn't talking.

**Architecture:** A small importable helper (`src/voice-agent/direct_mode_idle.py`) holds the pure decision (`should_revert`) + the revert action (`revert_to_claude`, via `systemd-run --user --scope` to escape the unit cgroup) + a watcher coroutine. Both `bin/jarvis-gemini-tools` and `bin/jarvis-gpt-tools` track a `last_activity` timestamp (reset on JARVIS audio-out, tool calls, and OpenAI speech events) and run the watcher in their existing task group.

**Tech Stack:** Python 3.13 (voice-agent venv), asyncio, systemd-run (v260), pytest.

Spec: `docs/superpowers/specs/2026-05-30-direct-mode-idle-revert-design.md`

---

## File Structure

- **Create** `src/voice-agent/direct_mode_idle.py` — idle decision + revert + watcher (importable by both bin backends, which already prepend the voice-agent dir to `sys.path`).
- **Create** `src/voice-agent/tests/test_direct_mode_idle.py` — unit tests for `should_revert` + `idle_timeout_s`.
- **Modify** `bin/jarvis-gemini-tools` — add `last_activity` tracking + watcher task.
- **Modify** `bin/jarvis-gpt-tools` — add `last_activity` tracking + watcher task.

---

### Task 1: Shared idle helper (TDD)

**Files:**
- Create: `src/voice-agent/direct_mode_idle.py`
- Test: `src/voice-agent/tests/test_direct_mode_idle.py`

- [ ] **Step 1: Write the failing test**

```python
# src/voice-agent/tests/test_direct_mode_idle.py
import os
import pytest
from direct_mode_idle import should_revert, idle_timeout_s


def test_disabled_when_timeout_zero():
    assert should_revert(idle_s=9999, timeout_s=0, tool_running=False) is False

def test_blocked_while_tool_running():
    assert should_revert(idle_s=9999, timeout_s=300, tool_running=True) is False

def test_no_revert_before_timeout():
    assert should_revert(idle_s=120, timeout_s=300, tool_running=False) is False

def test_no_revert_at_exact_timeout():
    assert should_revert(idle_s=300, timeout_s=300, tool_running=False) is False

def test_revert_past_timeout():
    assert should_revert(idle_s=301, timeout_s=300, tool_running=False) is True

def test_idle_timeout_default(monkeypatch):
    monkeypatch.delenv("JARVIS_DIRECT_IDLE_TIMEOUT_S", raising=False)
    assert idle_timeout_s() == 300.0

def test_idle_timeout_env_override(monkeypatch):
    monkeypatch.setenv("JARVIS_DIRECT_IDLE_TIMEOUT_S", "120")
    assert idle_timeout_s() == 120.0

def test_idle_timeout_bad_value_falls_back(monkeypatch):
    monkeypatch.setenv("JARVIS_DIRECT_IDLE_TIMEOUT_S", "not-a-number")
    assert idle_timeout_s() == 300.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_direct_mode_idle.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'direct_mode_idle'`

- [ ] **Step 3: Write the helper module**

```python
# src/voice-agent/direct_mode_idle.py
"""Idle auto-revert for the direct voice modes (gemini / openai).

When a direct-mode backend has had no activity for
JARVIS_DIRECT_IDLE_TIMEOUT_S seconds, revert to JARVIS-Claude (the free,
always-on base mode) so provider quota doesn't burn while the user is idle.
See docs/superpowers/specs/2026-05-30-direct-mode-idle-revert-design.md.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
from typing import Callable


def idle_timeout_s() -> float:
    """Idle window before reverting. Env JARVIS_DIRECT_IDLE_TIMEOUT_S
    (default 300; 0 disables). Bad values fall back to the default."""
    raw = os.environ.get("JARVIS_DIRECT_IDLE_TIMEOUT_S", "300")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 300.0


def should_revert(idle_s: float, timeout_s: float, tool_running: bool) -> bool:
    """Pure decision: revert iff enabled, no tool in flight, and idle past
    the window. Boundary is strict (idle must EXCEED timeout)."""
    return timeout_s > 0 and not tool_running and idle_s > timeout_s


def revert_to_claude(jarvis_mode_path: str, log) -> None:
    """Switch back to JARVIS-Claude.

    MUST run `jarvis-mode jarvis` in a SEPARATE cgroup: the backend's unit is
    KillMode=control-group + Restart=always, so a plain child would be killed
    by the `systemctl stop` that jarvis-mode issues — before it can unmute
    Claude. `systemd-run --user --scope` registers an independent scope that
    survives the stop. Falls back to a detached spawn if systemd-run is absent.
    """
    cmd = ["systemd-run", "--user", "--scope", "--", jarvis_mode_path, "jarvis"]
    try:
        subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return
    except Exception as e:
        log.warning(f"[idle-revert] systemd-run failed ({e!r}); trying direct spawn")
    try:
        subprocess.Popen(
            [jarvis_mode_path, "jarvis"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as e:
        log.warning(f"[idle-revert] fallback spawn failed: {e!r}")


async def idle_revert_watch(
    *,
    get_idle_s: Callable[[], float],
    is_tool_running: Callable[[], bool],
    jarvis_mode_path: str,
    stop: asyncio.Event,
    log,
    label: str,
) -> None:
    """Poll until idle exceeds the timeout (and no tool is running), then
    revert to Claude and set `stop` so the backend winds down."""
    timeout = idle_timeout_s()
    if timeout <= 0:
        log.info(f"[{label}] idle-revert disabled (JARVIS_DIRECT_IDLE_TIMEOUT_S=0)")
        return
    log.info(f"[{label}] idle-revert armed: → Claude after {timeout:.0f}s idle")
    poll = min(20.0, max(5.0, timeout / 4.0))
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=poll)
            return  # stop set elsewhere (deliberate shutdown)
        except asyncio.TimeoutError:
            pass
        if should_revert(get_idle_s(), timeout, is_tool_running()):
            log.warning(
                f"[{label}] idle {get_idle_s():.0f}s > {timeout:.0f}s — "
                f"reverting to JARVIS-Claude to stop token burn"
            )
            revert_to_claude(jarvis_mode_path, log)
            stop.set()
            return
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_direct_mode_idle.py -q`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/direct_mode_idle.py src/voice-agent/tests/test_direct_mode_idle.py
git commit -m "feat(direct-mode): idle-revert helper (should_revert + systemd-run --scope revert)"
```

---

### Task 2: Wire idle watcher into Gemini backend

**Files:**
- Modify: `bin/jarvis-gemini-tools`

- [ ] **Step 1: Add the import + jarvis-mode path resolver**

Near the other `import` lines / config block at top of `bin/jarvis-gemini-tools`, add:

```python
from direct_mode_idle import idle_revert_watch

# jarvis-mode lives next to this script (same bin/ dir).
JARVIS_MODE_BIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jarvis-mode")
```

(`direct_mode_idle` resolves because the script already does `sys.path.insert(0, VOICE_AGENT_DIR)`.)

- [ ] **Step 2: Add the `last_activity` cell next to `last_audio_at`**

Find `last_audio_at = [0.0]` (≈ line 478) and add directly below it:

```python
        last_activity = [loop.time()]  # monotonic; reset on any user/model/tool activity
```

- [ ] **Step 3: Reset `last_activity` on model audio-out**

Find the line `last_audio_at[0] = loop.time()` (≈ line 551, in `drain_replies`'s audio path) and add directly below it:

```python
                        last_activity[0] = loop.time()
```

- [ ] **Step 4: Reset `last_activity` on a tool call**

Find `status.set_tool_running(True)` (≈ line 523, in the `tool_call` branch) and add directly below it:

```python
                        last_activity[0] = loop.time()
```

- [ ] **Step 5: Add the watcher to the task group**

In the `tasks = [ ... ]` list (≈ line 573), add a new entry alongside the others (before the `stop` task):

```python
            asyncio.create_task(idle_revert_watch(
                get_idle_s=lambda: loop.time() - last_activity[0],
                is_tool_running=lambda: status._tool_running,
                jarvis_mode_path=JARVIS_MODE_BIN,
                stop=stop, log=log, label="gemini-tools",
            ), name="idle-revert"),
```

- [ ] **Step 6: Compile-check**

Run: `src/voice-agent/.venv/bin/python -m py_compile bin/jarvis-gemini-tools`
Expected: no output (success)

- [ ] **Step 7: Commit**

```bash
git add bin/jarvis-gemini-tools
git commit -m "feat(gemini-tools): idle auto-revert to Claude (stop idle token burn)"
```

---

### Task 3: Wire idle watcher into OpenAI backend

**Files:**
- Modify: `bin/jarvis-gpt-tools`

- [ ] **Step 1: Add the import + jarvis-mode path resolver**

Near the top imports/config of `bin/jarvis-gpt-tools`, add:

```python
from direct_mode_idle import idle_revert_watch

JARVIS_MODE_BIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jarvis-mode")
```

- [ ] **Step 2: Add the `last_activity` cell**

In `main()` after `loop = asyncio.get_running_loop()` (≈ line 390), add:

```python
    last_activity = [loop.time()]  # monotonic; reset on speech/model/tool activity
```

- [ ] **Step 3: Reset on OpenAI speech events**

In `drain_replies`, in the `input_audio_buffer.speech_started` handler (≈ line 586) and the `speech_stopped` handler (≈ line 620), add as the first line of each branch:

```python
                    last_activity[0] = loop.time()
```

- [ ] **Step 4: Reset on model audio-out**

In the audio-delta handler (where `status.set_speaking(True)` is called for output audio), add directly after the existing `status.set_speaking(True)`:

```python
                        last_activity[0] = loop.time()
```

- [ ] **Step 5: Reset on a tool call**

In the `response.function_call_arguments.done` handler, right after `status.set_tool_running(True)`, add:

```python
                    last_activity[0] = loop.time()
```

- [ ] **Step 6: Add the watcher to the task group**

In the `tasks = [ ... ]` list (≈ line 711), add before the `stop` task:

```python
            asyncio.create_task(idle_revert_watch(
                get_idle_s=lambda: loop.time() - last_activity[0],
                is_tool_running=lambda: status._tool_running,
                jarvis_mode_path=JARVIS_MODE_BIN,
                stop=stop, log=log, label="gpt-tools",
            ), name="idle-revert"),
```

- [ ] **Step 7: Compile-check**

Run: `src/voice-agent/.venv/bin/python -m py_compile bin/jarvis-gpt-tools`
Expected: no output (success)

- [ ] **Step 8: Commit**

```bash
git add bin/jarvis-gpt-tools
git commit -m "feat(gpt-tools): idle auto-revert to Claude (stop idle token burn)"
```

---

### Task 4: Verify + deploy

- [ ] **Step 1: Full helper test + compile both backends**

Run:
```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_direct_mode_idle.py -q
.venv/bin/python -m py_compile ../../bin/jarvis-gemini-tools ../../bin/jarvis-gpt-tools
```
Expected: tests PASS; py_compile silent.

- [ ] **Step 2: Deploy with a short timeout for a live smoke test**

Restart voice-client (for `:8767`), then start gemini with a 60 s timeout to observe quickly:
```bash
systemctl --user restart jarvis-voice-client.service; sleep 5
JARVIS_DIRECT_IDLE_TIMEOUT_S=60 bin/jarvis-mode gemini
```
(Or set the env in the unit/launch path for a persistent value.)

- [ ] **Step 3: Observe the idle revert in the log**

Run: `journalctl --user -u jarvis-gemini-tools.service --since "2 min ago" --no-pager | grep -iE "idle-revert|reverting"`
Expected: `idle-revert armed` on start; after ~60 s idle, `reverting to JARVIS-Claude…`; then `active-mode` → `jarvis` and the unit goes inactive (NOT restarted).

- [ ] **Step 4: Confirm a deliberate switch still works (no false revert path)**

Run: `bin/jarvis-mode gemini` then immediately `bin/jarvis-mode jarvis` — confirm clean switch both ways (the `systemctl stop` path is unaffected by the watcher).

- [ ] **Step 5: Commit any deploy-config change (if the timeout is persisted in a unit/launch file)**

```bash
git add -A && git commit -m "chore: set JARVIS_DIRECT_IDLE_TIMEOUT_S default for direct modes"
```
(Skip if the 300 s code default is left as-is with no config file change.)

---

## Self-Review

- **Spec coverage:** activity tracking (Tasks 2–3 steps), idle watcher (Task 1 `idle_revert_watch` + wiring), revert via `systemd-run --scope` (Task 1 `revert_to_claude`), config env (Task 1 `idle_timeout_s`), tool-running guard (`should_revert`), testable core (Task 1 tests), both backends (Tasks 2–3), Claude untouched (no Claude task) — all covered.
- **Placeholders:** none — every code/edit step shows actual code; verification steps show commands + expected output.
- **Type consistency:** `idle_revert_watch` keyword params (`get_idle_s`, `is_tool_running`, `jarvis_mode_path`, `stop`, `log`, `label`) match the call sites in Tasks 2 & 3; `should_revert(idle_s, timeout_s, tool_running)` signature matches its tests and its use inside `idle_revert_watch`; `JARVIS_MODE_BIN` defined identically in both backends.
