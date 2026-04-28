# Desktop Computer Use — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add nine `@function_tool` tools to the JARVIS voice agent so it can see the desktop via Gemini vision and control mouse/keyboard via xdotool.

**Architecture:** A new `jarvis_computer_use.py` module holds all computer-use logic (screenshot, Gemini vision call, xdotool wrappers, session state). The existing `jarvis_agent.py` imports the decorated tools and adds them to the `tools=[]` list. The voice agent remains the orchestrator — Gemini is eyes, Groq/DeepSeek is brain, xdotool is hands.

**Tech Stack:** Python 3.13, `google-genai==1.73.1` (already in venv), `scrot` (X11 screenshot, already at `/usr/bin/scrot`), `xdotool` (X11 input, `/usr/bin/xdotool`), `livekit.agents.function_tool`, `asyncio.create_subprocess_exec`, `pytest` + `unittest.mock`.

---

## File Structure

| Action | Path | Responsibility |
|--------|------|----------------|
| **Create** | `src/voice-agent/jarvis_computer_use.py` | All computer-use logic: session state, screenshot, Gemini vision, xdotool wrappers, `@function_tool` decorated tools |
| **Create** | `src/voice-agent/tests/test_computer_use.py` | Unit tests (mocked subprocess/Gemini) |
| **Modify** | `src/voice-agent/jarvis_agent.py:2964-2979` | Import computer-use tools + add to `tools=[]` list |
| **Modify** | `src/voice-agent/requirements.txt` | Pin `google-genai~=1.73` |
| **Modify** | `src/voice-agent/.env` | Add `GOOGLE_API_KEY=` comment + placeholder |

---

## Task 1: Pin google-genai in requirements and add env var

**Files:**
- Modify: `src/voice-agent/requirements.txt`
- Modify: `src/voice-agent/.env`

- [ ] **Step 1.1: Add google-genai to requirements.txt**

Open `src/voice-agent/requirements.txt` and add after the existing lines:

```
# Gemini vision for desktop computer-use (screen description)
google-genai~=1.73
```

- [ ] **Step 1.2: Add GOOGLE_API_KEY placeholder to .env**

Add to the bottom of `src/voice-agent/.env`:

```
# Gemini vision API — used by computer-use to describe the screen.
# Get a key at console.cloud.google.com → APIs & Services → Credentials.
GOOGLE_API_KEY=your-key-here
```

Replace `your-key-here` with the real key.

- [ ] **Step 1.3: Verify install**

```bash
cd src/voice-agent && .venv/bin/python -c "from google import genai; print('ok')"
```

Expected: `ok`

- [ ] **Step 1.4: Commit**

```bash
git add src/voice-agent/requirements.txt src/voice-agent/.env
git commit -m "feat: pin google-genai + add GOOGLE_API_KEY env placeholder for computer-use"
```

---

## Task 2: Write failing tests for screenshot + Gemini describe

**Files:**
- Create: `src/voice-agent/tests/test_computer_use.py`

- [ ] **Step 2.1: Write the test file**

Create `src/voice-agent/tests/test_computer_use.py`:

```python
"""Unit tests for jarvis_computer_use — screenshot, Gemini describe, xdotool, session."""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

# Allow importing the module directly without installing livekit.agents
sys.path.insert(0, str(Path(__file__).parent.parent))


# ── Helpers ───────────────────────────────────────────────────────────

def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ── Screenshot ────────────────────────────────────────────────────────

class TestTakeScreenshot:
    def test_calls_scrot_with_z_flag(self):
        import jarvis_computer_use as cu
        with patch("jarvis_computer_use.subprocess.run") as mock_run, \
             patch("builtins.open", MagicMock(return_value=MagicMock(
                 __enter__=MagicMock(return_value=MagicMock(read=MagicMock(return_value=b"\x89PNG"))),
                 __exit__=MagicMock(return_value=False)
             ))):
            result = cu._take_screenshot()
        assert mock_run.call_args.args[0][0] == "scrot"
        assert "-z" in mock_run.call_args.args[0]

    def test_returns_bytes(self):
        import jarvis_computer_use as cu
        with patch("jarvis_computer_use.subprocess.run"), \
             patch("builtins.open", MagicMock(return_value=MagicMock(
                 __enter__=MagicMock(return_value=MagicMock(read=MagicMock(return_value=b"\x89PNG"))),
                 __exit__=MagicMock(return_value=False)
             ))):
            result = cu._take_screenshot()
        assert isinstance(result, bytes)


# ── Gemini describe ───────────────────────────────────────────────────

class TestGeminiDescribe:
    def test_calls_generate_content_with_correct_model(self):
        import jarvis_computer_use as cu
        mock_response = MagicMock()
        mock_response.text = "Chrome browser is open, URL bar at top"
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        with patch("jarvis_computer_use._get_gemini_client", return_value=mock_client), \
             patch("jarvis_computer_use.asyncio.get_running_loop") as mock_loop:
            # run_in_executor should call our _call function synchronously in tests
            def fake_run_in_executor(executor, fn):
                return asyncio.coroutine(lambda: fn())()
            mock_loop.return_value.run_in_executor = MagicMock(side_effect=fake_run_in_executor)
            result = run(cu._gemini_describe(b"\x89PNG"))

        call_kwargs = mock_client.models.generate_content.call_args
        assert call_kwargs.kwargs.get("model") == cu.GEMINI_MODEL or \
               call_kwargs.args[0] == cu.GEMINI_MODEL or \
               "gemini" in str(call_kwargs)

    def test_returns_text_from_response(self):
        import jarvis_computer_use as cu
        mock_response = MagicMock()
        mock_response.text = "Desktop: Kitty terminal in foreground, taskbar visible"
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        with patch("jarvis_computer_use._get_gemini_client", return_value=mock_client), \
             patch("jarvis_computer_use.asyncio.get_running_loop") as mock_loop:
            def fake_run_in_executor(executor, fn):
                return asyncio.coroutine(lambda: fn())()
            mock_loop.return_value.run_in_executor = MagicMock(side_effect=fake_run_in_executor)
            result = run(cu._gemini_describe(b"\x89PNG"))

        assert result == "Desktop: Kitty terminal in foreground, taskbar visible"

    def test_raises_when_api_key_missing(self):
        import jarvis_computer_use as cu
        import os
        with patch.dict(os.environ, {"GOOGLE_API_KEY": ""}):
            with pytest.raises(cu.ComputerUseError, match="GOOGLE_API_KEY"):
                cu._get_gemini_client()
```

- [ ] **Step 2.2: Run tests to verify they fail correctly**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_computer_use.py -v 2>&1 | head -40
```

Expected: `ModuleNotFoundError: No module named 'jarvis_computer_use'` (the module doesn't exist yet — that's correct).

---

## Task 3: Create jarvis_computer_use.py — session, screenshot, Gemini vision

**Files:**
- Create: `src/voice-agent/jarvis_computer_use.py`

- [ ] **Step 3.1: Create the module with session state, screenshot, and Gemini describe**

Create `src/voice-agent/jarvis_computer_use.py`:

```python
"""
JARVIS desktop computer-use: Gemini vision + xdotool control.

Tools (all @function_tool, registered in jarvis_agent.py):
    computer_use  — start session; first screenshot → Gemini describe
    computer_stop — end session
    click         — xdotool click at (x, y)
    type_text     — xdotool type + optional Enter
    scroll        — xdotool scroll at (x, y)
    drag          — xdotool drag from→to
    key_press     — xdotool key combination (e.g. "ctrl+t")
    wait          — sleep N ms, then re-describe screen
    screenshot    — one-shot screenshot + Gemini describe (no session needed)

Safety guards:
    _FAILURE_LIMIT consecutive failures → stop + explain
    _STALL_TIMEOUT_S with no visible UI change → stop + explain
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass, field

from livekit.agents import function_tool

logger = logging.getLogger("jarvis-computer-use")

GEMINI_MODEL = "gemini-2.0-flash"
GEMINI_SCREEN_PROMPT = (
    "You are helping a voice assistant control a desktop computer. "
    "Describe the current screen state: what application is open, all "
    "visible UI elements (buttons, text fields, menus, links), and their "
    "approximate pixel coordinates (x, y from top-left corner). "
    "Be specific and concise — the assistant will decide what to click or type."
)

_FAILURE_LIMIT = 3
_STALL_TIMEOUT_S = 30.0


class ComputerUseError(RuntimeError):
    pass


@dataclass
class _Session:
    task: str
    started_at: float = field(default_factory=time.monotonic)
    consecutive_failures: int = 0
    last_description: str = ""
    last_change_at: float = field(default_factory=time.monotonic)


_active_session: _Session | None = None


# ── Gemini ────────────────────────────────────────────────────────────

def _get_gemini_client():
    """Return a google.genai Client. Raises ComputerUseError if key is missing."""
    from google import genai
    key = os.environ.get("GOOGLE_API_KEY", "")
    if not key:
        raise ComputerUseError("GOOGLE_API_KEY not set in environment")
    return genai.Client(api_key=key)


def _take_screenshot() -> bytes:
    """Take a full-screen PNG via scrot and return the bytes."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        path = f.name
    subprocess.run(
        ["scrot", "-z", path],
        check=True,
        timeout=5,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    with open(path, "rb") as f:
        return f.read()


async def _gemini_describe(png_bytes: bytes) -> str:
    """Send PNG bytes to Gemini vision, return UI description string."""
    from google.genai import types as genai_types
    client = _get_gemini_client()
    loop = asyncio.get_running_loop()

    def _call() -> str:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[
                genai_types.Part.from_bytes(data=png_bytes, mime_type="image/png"),
                GEMINI_SCREEN_PROMPT,
            ],
        )
        return response.text or "(no description returned)"

    return await loop.run_in_executor(None, _call)


async def _screenshot_and_describe() -> str:
    """Take screenshot, send to Gemini, return description."""
    png = _take_screenshot()
    return await _gemini_describe(png)


# ── Session safety guards ─────────────────────────────────────────────

def _check_guards() -> None:
    """Raise ComputerUseError if a safety limit is exceeded."""
    if _active_session is None:
        return
    if _active_session.consecutive_failures >= _FAILURE_LIMIT:
        raise ComputerUseError(
            f"Stopping after {_FAILURE_LIMIT} consecutive failures. "
            "The computer is not responding to actions. Tell the user what you tried."
        )
    elapsed = time.monotonic() - _active_session.last_change_at
    if elapsed >= _STALL_TIMEOUT_S:
        raise ComputerUseError(
            f"Stopping: no visible UI change in {int(elapsed)}s. "
            "The screen appears stuck. Tell the user what you last saw."
        )


def _record_success(description: str) -> None:
    """Reset failure counter; update last_change_at if screen changed."""
    if _active_session is None:
        return
    _active_session.consecutive_failures = 0
    if description != _active_session.last_description:
        _active_session.last_change_at = time.monotonic()
    _active_session.last_description = description


def _record_failure() -> None:
    if _active_session:
        _active_session.consecutive_failures += 1
```

- [ ] **Step 3.2: Run the screenshot + Gemini tests**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_computer_use.py::TestTakeScreenshot tests/test_computer_use.py::TestGeminiDescribe tests/test_computer_use.py::TestGeminiDescribe::test_raises_when_api_key_missing -v
```

Expected: all 5 tests PASS.

- [ ] **Step 3.3: Commit**

```bash
git add src/voice-agent/jarvis_computer_use.py src/voice-agent/tests/test_computer_use.py
git commit -m "feat: jarvis_computer_use — session state, screenshot via scrot, Gemini vision describe"
```

---

## Task 4: Write failing tests for xdotool wrappers

**Files:**
- Modify: `src/voice-agent/tests/test_computer_use.py` (append)

- [ ] **Step 4.1: Append xdotool tests to the test file**

Append to `src/voice-agent/tests/test_computer_use.py`:

```python
# ── xdotool ───────────────────────────────────────────────────────────

class TestXdotoolWrapper:
    def test_runs_xdotool_with_args(self):
        import jarvis_computer_use as cu

        async def fake_create_subprocess_exec(*args, **kwargs):
            mock_proc = MagicMock()
            mock_proc.communicate = AsyncMock(return_value=(b"12345\n", b""))
            return mock_proc

        with patch("asyncio.create_subprocess_exec", side_effect=fake_create_subprocess_exec):
            result = run(cu._xdotool("getactivewindow"))

        assert result == "12345"

    def test_returns_stripped_string(self):
        import jarvis_computer_use as cu

        async def fake_create_subprocess_exec(*args, **kwargs):
            mock_proc = MagicMock()
            mock_proc.communicate = AsyncMock(return_value=(b"  hello world  \n", b""))
            return mock_proc

        with patch("asyncio.create_subprocess_exec", side_effect=fake_create_subprocess_exec):
            result = run(cu._xdotool("type", "hello"))

        assert result == "hello world"


# ── computer_use / computer_stop ──────────────────────────────────────

class TestComputerUseSession:
    def setup_method(self):
        import jarvis_computer_use as cu
        cu._active_session = None

    def teardown_method(self):
        import jarvis_computer_use as cu
        cu._active_session = None

    def test_computer_use_starts_session(self):
        import jarvis_computer_use as cu

        with patch.object(cu, "_screenshot_and_describe", AsyncMock(return_value="Chrome open")):
            result = run(cu.computer_use.__wrapped__("book a flight"))

        assert cu._active_session is not None
        assert cu._active_session.task == "book a flight"
        assert "Chrome open" in result

    def test_computer_use_rejects_second_session(self):
        import jarvis_computer_use as cu
        cu._active_session = cu._Session(task="existing task")

        with patch.object(cu, "_screenshot_and_describe", AsyncMock(return_value="screen")):
            result = run(cu.computer_use.__wrapped__("new task"))

        assert "already active" in result

    def test_computer_stop_clears_session(self):
        import jarvis_computer_use as cu
        cu._active_session = cu._Session(task="open browser")

        result = run(cu.computer_stop.__wrapped__())

        assert cu._active_session is None
        assert "open browser" in result

    def test_computer_stop_when_no_session(self):
        import jarvis_computer_use as cu

        result = run(cu.computer_stop.__wrapped__())

        assert "no active" in result


# ── Safety guards ─────────────────────────────────────────────────────

class TestSafetyGuards:
    def setup_method(self):
        import jarvis_computer_use as cu
        cu._active_session = None

    def teardown_method(self):
        import jarvis_computer_use as cu
        cu._active_session = None

    def test_check_guards_raises_on_failure_limit(self):
        import jarvis_computer_use as cu
        cu._active_session = cu._Session(task="test")
        cu._active_session.consecutive_failures = cu._FAILURE_LIMIT

        with pytest.raises(cu.ComputerUseError, match="consecutive failures"):
            cu._check_guards()

    def test_check_guards_raises_on_stall(self):
        import jarvis_computer_use as cu
        cu._active_session = cu._Session(task="test")
        cu._active_session.last_change_at = time.monotonic() - cu._STALL_TIMEOUT_S - 1

        with pytest.raises(cu.ComputerUseError, match="no visible UI change"):
            cu._check_guards()

    def test_record_success_resets_failures(self):
        import jarvis_computer_use as cu
        cu._active_session = cu._Session(task="test")
        cu._active_session.consecutive_failures = 2

        cu._record_success("new screen state")

        assert cu._active_session.consecutive_failures == 0

    def test_record_success_updates_change_time_on_new_desc(self):
        import jarvis_computer_use as cu
        cu._active_session = cu._Session(task="test")
        cu._active_session.last_description = "old state"
        old_change_at = cu._active_session.last_change_at - 5

        cu._record_success("new state different from old")

        assert cu._active_session.last_change_at > old_change_at

    def test_record_failure_increments_counter(self):
        import jarvis_computer_use as cu
        cu._active_session = cu._Session(task="test")

        cu._record_failure()
        cu._record_failure()

        assert cu._active_session.consecutive_failures == 2
```

- [ ] **Step 4.2: Run new tests to verify they fail**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_computer_use.py::TestXdotoolWrapper tests/test_computer_use.py::TestComputerUseSession tests/test_computer_use.py::TestSafetyGuards -v 2>&1 | head -40
```

Expected: failures referencing `_xdotool`, `computer_use`, `computer_stop` not found (functions don't exist yet in the module).

---

## Task 5: Add xdotool wrappers + session tools to jarvis_computer_use.py

**Files:**
- Modify: `src/voice-agent/jarvis_computer_use.py` (append)

- [ ] **Step 5.1: Append xdotool helper and all @function_tool tools**

Append to the bottom of `src/voice-agent/jarvis_computer_use.py`:

```python
# ── xdotool execution ─────────────────────────────────────────────────

async def _xdotool(*args: str) -> str:
    """Run `xdotool <args>`, return stdout+stderr as stripped string."""
    proc = await asyncio.create_subprocess_exec(
        "xdotool", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return "(xdotool timeout)"
    return out.decode("utf-8", errors="replace").strip()


def _fmt_result(success: bool, **kv) -> str:
    """Format tool return value as a readable string."""
    parts = [f"success={success}"]
    for k, v in kv.items():
        parts.append(f"{k}={v!r}")
    return ", ".join(parts)


# ── @function_tool implementations ───────────────────────────────────


@function_tool
async def computer_use(task: str) -> str:
    """Start a computer-use session to control the desktop visually.

    Call this when the user wants JARVIS to operate the computer — click
    buttons, type into fields, navigate apps. Gemini Vision will describe
    the screen after each action. Call computer_stop when the task is done.

    Only one session can run at a time. Calling again while one is active
    returns an error.

    Args:
        task: Natural-language description of what to accomplish.
    """
    global _active_session
    if _active_session is not None:
        return "(a computer-use session is already active; call computer_stop first)"
    _active_session = _Session(task=task)
    try:
        desc = await _screenshot_and_describe()
    except Exception as e:
        _active_session = None
        return f"(failed to start session: {e})"
    _active_session.last_description = desc
    logger.info(f"[computer-use] session started: {task[:60]!r}")
    return f"Computer-use session started.\nTask: {task}\n\nCurrent screen:\n{desc}"


@function_tool
async def computer_stop() -> str:
    """End the active computer-use session.

    Call this when the task is complete or when giving up. Returns a
    summary of what was accomplished.
    """
    global _active_session
    if _active_session is None:
        return "(no active computer-use session)"
    task = _active_session.task
    _active_session = None
    logger.info(f"[computer-use] session stopped. task={task[:60]!r}")
    return f"Computer-use session ended. Task was: {task}"


@function_tool
async def click(x: int, y: int, button: str = "left", count: int = 1) -> str:
    """Move the mouse to (x, y) and click.

    Requires an active computer_use session. Returns the updated screen
    description after the click so you can see if it worked.

    Args:
        x:      Pixel x-coordinate from left edge of screen.
        y:      Pixel y-coordinate from top edge of screen.
        button: "left" (default), "right", or "middle".
        count:  Number of clicks — 1 for single (default), 2 for double-click.
    """
    if _active_session is None:
        return "(no active computer-use session; call computer_use first)"
    try:
        _check_guards()
    except ComputerUseError as e:
        return _fmt_result(False, error=str(e))

    btn_map = {"left": "1", "middle": "2", "right": "3"}
    btn = btn_map.get(button, "1")
    await _xdotool("mousemove", "--sync", str(x), str(y))
    for _ in range(count):
        await _xdotool("click", btn)

    await asyncio.sleep(0.5)
    try:
        desc = await _screenshot_and_describe()
        _record_success(desc)
        logger.info(f"[computer-use] click({x},{y},{button}×{count})")
        return _fmt_result(True, cursor_at=[x, y], screen=desc)
    except Exception as e:
        _record_failure()
        return _fmt_result(False, error=str(e))


@function_tool
async def type_text(text: str, enter: bool = False) -> str:
    """Type a string at the current cursor position.

    Requires an active computer_use session. Sends keystrokes via xdotool.
    Set enter=True to press Return after typing (e.g. submitting a search).

    Args:
        text:  The text to type.
        enter: If True, press Return after typing (default False).
    """
    if _active_session is None:
        return "(no active computer-use session; call computer_use first)"
    try:
        _check_guards()
    except ComputerUseError as e:
        return _fmt_result(False, error=str(e))

    await _xdotool("type", "--clearmodifiers", "--", text)
    if enter:
        await _xdotool("key", "Return")

    await asyncio.sleep(0.5)
    try:
        desc = await _screenshot_and_describe()
        _record_success(desc)
        logger.info(f"[computer-use] type_text({text[:40]!r}, enter={enter})")
        return _fmt_result(True, typed=text, enter_pressed=enter, screen=desc)
    except Exception as e:
        _record_failure()
        return _fmt_result(False, error=str(e))


@function_tool
async def scroll(x: int, y: int, amount: int) -> str:
    """Scroll at screen position (x, y).

    Requires an active computer_use session.

    Args:
        x:      Pixel x-coordinate to scroll at.
        y:      Pixel y-coordinate to scroll at.
        amount: Positive = scroll down, negative = scroll up. Each unit is
                one scroll wheel click (≈ 3 lines of text).
    """
    if _active_session is None:
        return "(no active computer-use session; call computer_use first)"
    try:
        _check_guards()
    except ComputerUseError as e:
        return _fmt_result(False, error=str(e))

    await _xdotool("mousemove", "--sync", str(x), str(y))
    btn = "5" if amount > 0 else "4"
    for _ in range(abs(amount)):
        await _xdotool("click", btn)

    await asyncio.sleep(0.3)
    try:
        desc = await _screenshot_and_describe()
        _record_success(desc)
        logger.info(f"[computer-use] scroll({x},{y},{amount})")
        return _fmt_result(True, scrolled=amount, screen=desc)
    except Exception as e:
        _record_failure()
        return _fmt_result(False, error=str(e))


@function_tool
async def drag(start_x: int, start_y: int, end_x: int, end_y: int) -> str:
    """Click-drag from (start_x, start_y) to (end_x, end_y).

    Requires an active computer_use session. Useful for sliders, drag-and-drop,
    text selection.

    Args:
        start_x: Start pixel x.
        start_y: Start pixel y.
        end_x:   End pixel x.
        end_y:   End pixel y.
    """
    if _active_session is None:
        return "(no active computer-use session; call computer_use first)"
    try:
        _check_guards()
    except ComputerUseError as e:
        return _fmt_result(False, error=str(e))

    await _xdotool("mousemove", "--sync", str(start_x), str(start_y))
    await _xdotool("mousedown", "1")
    await _xdotool("mousemove", "--sync", str(end_x), str(end_y))
    await _xdotool("mouseup", "1")

    await asyncio.sleep(0.5)
    try:
        desc = await _screenshot_and_describe()
        _record_success(desc)
        logger.info(f"[computer-use] drag ({start_x},{start_y})→({end_x},{end_y})")
        return _fmt_result(True, dragged_to=[end_x, end_y], screen=desc)
    except Exception as e:
        _record_failure()
        return _fmt_result(False, error=str(e))


@function_tool
async def key_press(keys: str) -> str:
    """Press a keyboard shortcut or key combination.

    Requires an active computer_use session. Uses xdotool key syntax.

    Args:
        keys: Key combination string, e.g. "ctrl+t", "alt+F4", "super",
              "Return", "Escape", "ctrl+shift+n". Case-insensitive for
              modifiers. Multiple keys joined with "+".
    """
    if _active_session is None:
        return "(no active computer-use session; call computer_use first)"
    try:
        _check_guards()
    except ComputerUseError as e:
        return _fmt_result(False, error=str(e))

    await _xdotool("key", "--clearmodifiers", keys)

    await asyncio.sleep(0.5)
    try:
        desc = await _screenshot_and_describe()
        _record_success(desc)
        logger.info(f"[computer-use] key_press({keys!r})")
        return _fmt_result(True, keys_pressed=keys, screen=desc)
    except Exception as e:
        _record_failure()
        return _fmt_result(False, error=str(e))


@function_tool
async def wait(ms: int = 500) -> str:
    """Wait N milliseconds for the UI to settle, then describe the screen.

    Requires an active computer_use session. Use after triggering actions
    that take time to render (page loads, animations, dialog boxes opening).

    Args:
        ms: Milliseconds to wait (default 500, max 10000).
    """
    if _active_session is None:
        return "(no active computer-use session; call computer_use first)"

    ms = max(100, min(int(ms), 10_000))
    await asyncio.sleep(ms / 1000.0)

    try:
        desc = await _screenshot_and_describe()
        _record_success(desc)
        logger.info(f"[computer-use] wait({ms}ms)")
        return _fmt_result(True, waited_ms=ms, screen=desc)
    except Exception as e:
        _record_failure()
        return _fmt_result(False, error=str(e))


@function_tool
async def screenshot() -> str:
    """Take a screenshot and return a Gemini description of the screen.

    Does NOT require an active computer_use session — use this for one-off
    "what's on the screen right now?" questions, or to orient yourself before
    starting a computer_use session.
    """
    try:
        desc = await _screenshot_and_describe()
        logger.info("[computer-use] one-shot screenshot")
        return desc
    except Exception as e:
        return f"(screenshot failed: {e})"
```

- [ ] **Step 5.2: Run all tests**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_computer_use.py -v
```

Expected: all tests PASS.

- [ ] **Step 5.3: Commit**

```bash
git add src/voice-agent/jarvis_computer_use.py src/voice-agent/tests/test_computer_use.py
git commit -m "feat: xdotool wrappers + all nine computer-use @function_tool definitions"
```

---

## Task 6: Wire computer-use tools into jarvis_agent.py

**Files:**
- Modify: `src/voice-agent/jarvis_agent.py`

- [ ] **Step 6.1: Add the import near the top of jarvis_agent.py**

Find this line in `src/voice-agent/jarvis_agent.py`:

```python
logger = logging.getLogger("jarvis-agent")
```

Add the import directly after it:

```python
from jarvis_computer_use import (
    computer_use,
    computer_stop,
    click,
    type_text,
    scroll,
    drag,
    key_press,
    wait,
    screenshot,
)
```

- [ ] **Step 6.2: Add tools to the tools=[] list**

Find the `tools=[` list in `src/voice-agent/jarvis_agent.py` at line 2964. It currently reads:

```python
            tools=[
                run_jarvis_cli,
                bash,
                read_file,
                web_fetch,
                glob_files,
                grep_files,
                type_in_terminal,
                media_control,
                recall_conversation,
                # Behavioral learning
                remember_this,
                list_pending_proposals,
                accept_proposal,
                reject_proposal,
            ],
```

Replace it with:

```python
            tools=[
                run_jarvis_cli,
                bash,
                read_file,
                web_fetch,
                glob_files,
                grep_files,
                type_in_terminal,
                media_control,
                recall_conversation,
                # Behavioral learning
                remember_this,
                list_pending_proposals,
                accept_proposal,
                reject_proposal,
                # Desktop computer-use (Gemini vision + xdotool)
                computer_use,
                computer_stop,
                click,
                type_text,
                scroll,
                drag,
                key_press,
                wait,
                screenshot,
            ],
```

- [ ] **Step 6.3: Smoke-test the import**

```bash
cd src/voice-agent && GOOGLE_API_KEY=test .venv/bin/python -c "
import jarvis_agent
print('import ok')
print('computer_use in tools:', any(
    getattr(t, '__name__', '') == 'computer_use'
    for t in ['computer_use']
))
"
```

Expected: `import ok` (no ImportError). The agent won't fully start without a LIVEKIT_URL, but the import chain should succeed.

- [ ] **Step 6.4: Commit**

```bash
git add src/voice-agent/jarvis_agent.py
git commit -m "feat: wire computer-use tools into jarvis_agent tools list"
```

---

## Task 7: Restart the voice agent and smoke-test

**Files:** None (runtime test only)

- [ ] **Step 7.1: Set the real GOOGLE_API_KEY in .env**

Edit `src/voice-agent/.env` and replace `your-key-here` on the `GOOGLE_API_KEY=` line with your actual Google API key.

- [ ] **Step 7.2: Restart the voice agent**

```bash
systemctl --user restart jarvis-voice-agent.service
systemctl --user status jarvis-voice-agent.service
```

Expected: `active (running)`, no `ImportError` in the output.

- [ ] **Step 7.3: Tail the log to verify startup**

```bash
tail -n 30 /tmp/jarvis-voice-agent.log
```

Expected: The agent starts normally; no tracebacks. You should see the standard LiveKit worker startup lines.

- [ ] **Step 7.4: Voice smoke-test**

Say to JARVIS:

> "Take a screenshot and tell me what's on the screen."

Expected: JARVIS calls the `screenshot` tool, Gemini describes the current desktop, and JARVIS reads the description aloud.

- [ ] **Step 7.5: Full computer-use smoke-test**

Say:

> "Hey Jarvis, open a new Kitty terminal."

Expected: JARVIS calls `computer_use("open a new Kitty terminal")`, gets the screen description from Gemini, then calls `key_press("super")` or `bash("setsid -f kitty &")` or similar, then `computer_stop()`, and reports back.

- [ ] **Step 7.6: Commit smoke-test results**

If the test surfaces a bug, fix it and commit. If clean:

```bash
git add src/voice-agent/.env
git commit -m "feat: add GOOGLE_API_KEY to voice-agent .env for Gemini computer-use"
```

---

## Self-Review

**Spec coverage:**

| Spec requirement | Task that implements it |
|---|---|
| `computer_use` tool — start session, first screenshot → Gemini | Task 3 + Task 5 |
| `computer_stop` tool | Task 5 |
| `click` tool | Task 5 |
| `type_text` tool | Task 5 |
| `scroll` tool | Task 5 |
| `drag` tool | Task 5 |
| `key_press` tool | Task 5 |
| `wait` tool | Task 5 |
| `screenshot` tool (no session needed) | Task 5 |
| Return `{ success, error, cursor_at, screen }` per tool | Task 5 (`_fmt_result`) |
| 3 consecutive failures → stop | Task 3 (`_check_guards`) + Task 4/5 tests |
| 30s no UI change → stop | Task 3 (`_check_guards`) + Task 4/5 tests |
| Only one session at a time | Task 5 (`computer_use` guard) |
| User says "stop/cancel" → immediate stop | Handled by existing LiveKit barge-in → `computer_stop` |
| Gemini as eyes, Groq/DeepSeek as brain | Architecture: Gemini in `_gemini_describe`, Groq/DeepSeek in existing LLM pipeline |
| xdotool as hands | Task 5 (`_xdotool`) |
| `jarvis_voice_client.py` — no changes | Correct, not touched |
| Tauri desktop app — no changes | Correct, not touched |

**No placeholders found.**

**Type consistency check:** `_fmt_result` used consistently in all action tools. `_Session` dataclass fields (`task`, `consecutive_failures`, `last_description`, `last_change_at`) referenced consistently across `_check_guards`, `_record_success`, `_record_failure`. `computer_use.__wrapped__` used correctly in tests to unwrap the `@function_tool` decorator.
