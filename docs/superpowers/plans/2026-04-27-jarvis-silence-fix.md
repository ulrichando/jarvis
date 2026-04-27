# JARVIS Permanent Silence Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the two causes of JARVIS going silent: (1) AgentSession crashes on Groq STT network failures with no auto-recovery, and (2) a quiet-hours gate window that blocks legitimate voice turns in the evening and cuts off follow-up turns after 5 minutes.

**Architecture:** Both fixes touch a single file (`src/voice-agent/jarvis_agent.py`). Fix 1 extracts a module-level async restart helper and registers a `session.on("close")` watchdog that triggers a `jarvis-voice-client` systemd restart on unrecoverable errors. Fix 2 changes three constants and makes the follow-up window env-configurable. The voice client's existing `_agent_presence_watchdog` handles room deletion and fresh dispatch on reconnect — no voice client changes needed.

**Tech Stack:** Python 3.13, livekit-agents (`AgentSession`, `CloseEvent`), asyncio, subprocess (aliased as `_subprocess`, already imported), systemd user units.

---

## File Map

| File | Action | Purpose |
|---|---|---|
| `src/voice-agent/jarvis_agent.py` | Modify | All changes — constants (lines 210–212) and new watchdog |
| `src/voice-agent/tests/test_silence_fix.py` | Create | Unit tests for constants and restart helper |

---

## Task 1: Tighten quiet-hours gate constants

**Context:** Lines 210–212 of `jarvis_agent.py` define the quiet-hours window. Currently `QUIET_HOURS_START=23` (11pm), `QUIET_HOURS_END=7` (7am), `QUIET_HOURS_WINDOW_SEC=300` (5 min). These need to be 1, 6, and 1200 respectively. `QUIET_HOURS_WINDOW_SEC` also needs an env var so it can be tuned without code changes.

**Files:**
- Modify: `src/voice-agent/jarvis_agent.py:210-212`
- Create: `src/voice-agent/tests/test_silence_fix.py`

- [ ] **Step 1: Create the test file with failing constant tests**

```bash
mkdir -p /path/to/jarvis/src/voice-agent/tests
```

Create `src/voice-agent/tests/test_silence_fix.py`:

```python
"""Tests for the JARVIS silence fix — quiet-hours constants and session watchdog."""
import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

# Add voice-agent dir to path so we can import the module directly
sys.path.insert(0, str(Path(__file__).parent.parent))


class TestQuietHoursConstants:
    """Quiet-hours defaults match the tightened spec values."""

    def test_quiet_hours_start_default(self):
        # Must be 1 (1am) — not 23 (11pm). Tightening removes the 11pm-1am block.
        import importlib
        import jarvis_agent
        importlib.reload(jarvis_agent)
        assert jarvis_agent.QUIET_HOURS_START == 1, (
            f"Expected QUIET_HOURS_START=1, got {jarvis_agent.QUIET_HOURS_START}"
        )

    def test_quiet_hours_end_default(self):
        # Must be 6 (6am) — not 7 (7am). 6am-7am is morning, not sleep.
        import importlib
        import jarvis_agent
        importlib.reload(jarvis_agent)
        assert jarvis_agent.QUIET_HOURS_END == 6, (
            f"Expected QUIET_HOURS_END=6, got {jarvis_agent.QUIET_HOURS_END}"
        )

    def test_quiet_hours_window_default(self):
        # Must be 1200.0 (20 min) — not 300 (5 min). Natural pauses exceed 5 min.
        import importlib
        import jarvis_agent
        importlib.reload(jarvis_agent)
        assert jarvis_agent.QUIET_HOURS_WINDOW_SEC == 1200.0, (
            f"Expected QUIET_HOURS_WINDOW_SEC=1200.0, got {jarvis_agent.QUIET_HOURS_WINDOW_SEC}"
        )

    def test_quiet_hours_window_env_override(self):
        # JARVIS_QUIET_WINDOW_SEC env var must override the default.
        with patch.dict(os.environ, {"JARVIS_QUIET_WINDOW_SEC": "600"}):
            import importlib
            import jarvis_agent
            importlib.reload(jarvis_agent)
            assert jarvis_agent.QUIET_HOURS_WINDOW_SEC == 600.0, (
                f"Expected QUIET_HOURS_WINDOW_SEC=600.0 (from env), got {jarvis_agent.QUIET_HOURS_WINDOW_SEC}"
            )
```

- [ ] **Step 2: Run tests to verify they fail (current values are 23, 7, 300)**

```bash
cd src/voice-agent
.venv/bin/python -m pytest tests/test_silence_fix.py::TestQuietHoursConstants -v
```

Expected output: 3 FAILED (start=23, end=7, window=300), 1 FAILED or PASSED (env test may pass if float() works on current hardcoded value).

- [ ] **Step 3: Edit the three constants in `jarvis_agent.py` lines 210–212**

Current (lines 210–212):
```python
QUIET_HOURS_START      = int(os.environ.get("JARVIS_QUIET_START", "23"))  # 11pm
QUIET_HOURS_END        = int(os.environ.get("JARVIS_QUIET_END",   "7"))   # 7am
QUIET_HOURS_WINDOW_SEC = 300.0   # 5-min recency window for follow-ups
```

Replace with:
```python
QUIET_HOURS_START      = int(os.environ.get("JARVIS_QUIET_START",      "1"))    # 1am
QUIET_HOURS_END        = int(os.environ.get("JARVIS_QUIET_END",        "6"))    # 6am
QUIET_HOURS_WINDOW_SEC = float(os.environ.get("JARVIS_QUIET_WINDOW_SEC", "1200"))  # 20 min
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd src/voice-agent
.venv/bin/python -m pytest tests/test_silence_fix.py::TestQuietHoursConstants -v
```

Expected output:
```
PASSED tests/test_silence_fix.py::TestQuietHoursConstants::test_quiet_hours_start_default
PASSED tests/test_silence_fix.py::TestQuietHoursConstants::test_quiet_hours_end_default
PASSED tests/test_silence_fix.py::TestQuietHoursConstants::test_quiet_hours_window_default
PASSED tests/test_silence_fix.py::TestQuietHoursConstants::test_quiet_hours_window_env_override
4 passed
```

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/jarvis_agent.py src/voice-agent/tests/test_silence_fix.py
git commit -m "fix: tighten quiet-hours gate — 1am-6am window, 20min follow-up"
```

---

## Task 2: Add session crash watchdog (Fix 1)

**Context:** When Groq STT has a network failure, `AgentSession` closes with a non-None `error` (`CloseEvent.error: STTError | ...`). The agent worker process stays alive but the session is dead — JARVIS goes silent. We need to detect this and trigger `systemctl --user restart jarvis-voice-client`, which causes the voice client's existing `_agent_presence_watchdog` to delete the LiveKit room and reconnect, forcing a fresh job dispatch and new `AgentSession`.

The restart logic is extracted to a module-level async function `_restart_voice_client_after_crash()` so it can be unit-tested independently of `entrypoint()`.

**Files:**
- Modify: `src/voice-agent/jarvis_agent.py` — add `_restart_voice_client_after_crash()` near module top, add `@session.on("close")` handler inside `entrypoint()`
- Modify: `src/voice-agent/tests/test_silence_fix.py` — add watchdog tests

- [ ] **Step 1: Add watchdog tests to the test file**

Append to `src/voice-agent/tests/test_silence_fix.py`:

```python

class TestSessionWatchdog:
    """_restart_voice_client_after_crash calls Popen with the right systemctl command."""

    def test_restart_calls_popen(self):
        import importlib
        import jarvis_agent
        importlib.reload(jarvis_agent)

        with patch("jarvis_agent._subprocess.Popen") as mock_popen, \
             patch("asyncio.sleep", new=AsyncMock()):
            asyncio.run(jarvis_agent._restart_voice_client_after_crash())
            mock_popen.assert_called_once_with(
                ["systemctl", "--user", "restart", "jarvis-voice-client"],
                stdout=jarvis_agent._subprocess.DEVNULL,
                stderr=jarvis_agent._subprocess.DEVNULL,
            )

    def test_restart_is_nonblocking_popen(self):
        """Must use Popen (fire-and-forget), NOT check_call/run which would block."""
        import importlib
        import jarvis_agent
        importlib.reload(jarvis_agent)

        with patch("jarvis_agent._subprocess.Popen") as mock_popen, \
             patch("asyncio.sleep", new=AsyncMock()):
            asyncio.run(jarvis_agent._restart_voice_client_after_crash())
            # Popen called once — not check_call, not run
            assert mock_popen.call_count == 1
```

- [ ] **Step 2: Run watchdog tests to verify they fail (function doesn't exist yet)**

```bash
cd src/voice-agent
.venv/bin/python -m pytest tests/test_silence_fix.py::TestSessionWatchdog -v
```

Expected: `AttributeError: module 'jarvis_agent' has no attribute '_restart_voice_client_after_crash'`

- [ ] **Step 3: Add `_restart_voice_client_after_crash()` to `jarvis_agent.py`**

Find the block that starts at line ~210 (the quiet-hours constants). Add this new function immediately after `_recent_interaction()` (currently around line 232). The function goes between `_recent_interaction()` and the next block.

Locate this exact text in `jarvis_agent.py` (currently around line 232–233):
```python
def _recent_interaction() -> bool:
    return (time.monotonic() - _last_real_interaction) < QUIET_HOURS_WINDOW_SEC

```

Add after it (insert after the blank line that follows `_recent_interaction`):
```python

async def _restart_voice_client_after_crash() -> None:
    """3-second debounce then restart jarvis-voice-client via systemd.

    Called by _on_session_close when AgentSession dies with a non-None error.
    The voice client's _agent_presence_watchdog handles room deletion and
    fresh dispatch — we only need to trigger the restart.
    """
    await asyncio.sleep(3)
    _subprocess.Popen(
        ["systemctl", "--user", "restart", "jarvis-voice-client"],
        stdout=_subprocess.DEVNULL,
        stderr=_subprocess.DEVNULL,
    )

```

- [ ] **Step 4: Run watchdog tests to verify they pass**

```bash
cd src/voice-agent
.venv/bin/python -m pytest tests/test_silence_fix.py::TestSessionWatchdog -v
```

Expected output:
```
PASSED tests/test_silence_fix.py::TestSessionWatchdog::test_restart_calls_popen
PASSED tests/test_silence_fix.py::TestSessionWatchdog::test_restart_is_nonblocking_popen
2 passed
```

- [ ] **Step 5: Register `_on_session_close` handler inside `entrypoint()`**

Inside `entrypoint()`, find the end of the `_on_error` handler (currently around line 2746):
```python
            logger.warning(f"TTS error logged to {_tts_fail_marker}: {err}")
        except Exception as e:
            logger.debug(f"_on_error handler hiccup: {e}")
```

Add the new handler immediately after that block (before the blank line and `# Build the system prompt...` comment):

```python

    # ── Session crash watchdog ────────────────────────────────────────
    # When Groq STT has a transient network failure, the framework
    # retries 3 times then marks the session "unrecoverable". The worker
    # process stays alive but the AgentSession is dead — JARVIS goes
    # silent with no feedback. Detect this via CloseEvent.error and
    # trigger a voice-client restart so _agent_presence_watchdog forces
    # a fresh room + new AgentSession (~5-8 s total recovery time).
    @session.on("close")
    def _on_session_close(ev) -> None:
        error = getattr(ev, "error", None)
        if error is None:
            return  # clean shutdown (model switch, tray quit) — don't restart
        logger.error(
            f"[session-watchdog] AgentSession died with error: {error}. "
            "Scheduling voice-client restart in 3s."
        )
        asyncio.create_task(_restart_voice_client_after_crash())
```

- [ ] **Step 6: Run the full test suite to confirm nothing regressed**

```bash
cd src/voice-agent
.venv/bin/python -m pytest tests/test_silence_fix.py -v
```

Expected output:
```
PASSED tests/test_silence_fix.py::TestQuietHoursConstants::test_quiet_hours_start_default
PASSED tests/test_silence_fix.py::TestQuietHoursConstants::test_quiet_hours_end_default
PASSED tests/test_silence_fix.py::TestQuietHoursConstants::test_quiet_hours_window_default
PASSED tests/test_silence_fix.py::TestQuietHoursConstants::test_quiet_hours_window_env_override
PASSED tests/test_silence_fix.py::TestSessionWatchdog::test_restart_calls_popen
PASSED tests/test_silence_fix.py::TestSessionWatchdog::test_restart_is_nonblocking_popen
6 passed
```

- [ ] **Step 7: Restart the voice agent to apply all changes**

```bash
systemctl --user restart jarvis-voice-agent
```

Wait ~5 seconds, then confirm it came back:
```bash
systemctl --user status jarvis-voice-agent --no-pager | head -5
```

Expected: `active (running)` with a recent timestamp.

- [ ] **Step 8: Verify the watchdog is wired in the running agent**

```bash
grep "session-watchdog\|QUIET_HOURS" /tmp/jarvis-voice-agent.log | tail -5
```

The quiet-hours gate is only logged when a turn is dropped, so it won't appear immediately. But confirm no startup errors are present.

- [ ] **Step 9: Commit**

```bash
git add src/voice-agent/jarvis_agent.py src/voice-agent/tests/test_silence_fix.py
git commit -m "fix: auto-recover from AgentSession crash via session-watchdog"
```

---

## Success Criteria Verification

After both tasks are complete, verify against the spec:

| Criterion | How to check |
|---|---|
| STT failure → auto-recovery within 10s | Watch log: `tail -f /tmp/jarvis-voice-agent.log` during next STT hiccup. Expect `[session-watchdog]` log line followed by new `AgentSession started` within ~8s. |
| 11pm–1am responds without "Jarvis" on every turn | Say something between 11pm–1am without vocative. Should respond. |
| 20-min follow-up window | Start a conversation, wait 10 min, say a follow-up without "Jarvis". Should respond. |
| 1am–6am gate still blocks ambient | No response between 1am–6am unless "Jarvis" vocative is used. |
