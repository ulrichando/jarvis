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
    _FAILURE_LIMIT consecutive failures → stop and explain
    _STALL_TIMEOUT_S with no visible UI change → stop and explain
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

# gemini-3.1-flash-lite-preview — Gemini 3.1 Flash family with vision.
# Model selection rationale (verified 2026-04-28):
#   - gemini-3.1-flash-live-preview: returns 1011 INTERNAL_ERROR on
#     the Live websocket regardless of config/api-version. Model is
#     listed but its backend is not serving requests yet on this key.
#     Swap back here when Google fixes it — call shape is the same as
#     the regular Live API flow.
#   - gemini-3-flash-preview: works with generate_content but ~2x slower
#     than the lite variant.
#   - gemini-2.0-flash: returns 429 RESOURCE_EXHAUSTED (free-tier limit 0).
#   - gemini-2.5-flash / gemini-2.5-flash-lite: work but are an older family.
GEMINI_MODEL = "gemini-3.1-flash-lite-preview"

# Default video device for webcam_capture. Override via JARVIS_WEBCAM_DEVICE.
WEBCAM_DEVICE = os.environ.get("JARVIS_WEBCAM_DEVICE", "/dev/video0")
WEBCAM_RESOLUTION = os.environ.get("JARVIS_WEBCAM_RES", "1280x720")
WEBCAM_PROMPT = (
    "You are JARVIS's eyes via the webcam. Describe what you see: "
    "people present (count, posture, facing direction, expression), "
    "the room/environment, anything notable. Be specific and concise."
)
GEMINI_SCREEN_PROMPT = (
    "You are helping a voice assistant control a desktop computer. "
    "Describe the current screen state: what application is open, all "
    "visible UI elements (buttons, text fields, menus, links), and their "
    "approximate pixel coordinates (x, y from top-left corner). "
    "Be specific and concise — the assistant will decide what to click or type."
)

_FAILURE_LIMIT = 3
_STALL_TIMEOUT_S = 30.0
# Tray writes this file when the user clicks "Stop Computer Use".
# _check_guards reads + unlinks it on the next action.
_STOP_SIGNAL_FILE = os.path.expanduser("~/.jarvis/computer-use-stop")


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
    """Return a google.genai Client. Raise ComputerUseError if key missing."""
    from google import genai
    key = os.environ.get("GOOGLE_API_KEY", "")
    if not key:
        raise ComputerUseError("GOOGLE_API_KEY not set in environment")
    return genai.Client(api_key=key)


def _take_screenshot() -> bytes:
    """Take a full-screen PNG via scrot, return the bytes."""
    # NamedTemporaryFile pre-creates the file; without `-o` scrot
    # refuses to overwrite and silently writes to <name>_000.png
    # instead, leaving the path we read empty (0 bytes).
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        path = f.name
    try:
        subprocess.run(
            ["scrot", "-o", path],
            check=True,
            timeout=5,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        with open(path, "rb") as f:
            return f.read()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


async def _gemini_describe(
    image_bytes: bytes,
    mime_type: str = "image/png",
    prompt: str = GEMINI_SCREEN_PROMPT,
) -> str:
    """Send image bytes to Gemini vision, return description string."""
    from google.genai import types as genai_types
    client = _get_gemini_client()
    loop = asyncio.get_running_loop()

    def _call() -> str:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[
                genai_types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                prompt,
            ],
        )
        return response.text or "(no description returned)"

    return await loop.run_in_executor(None, _call)


async def _screenshot_and_describe() -> str:
    """Take screenshot, send to Gemini, return description."""
    png = _take_screenshot()
    return await _gemini_describe(png)


def _take_webcam_frame() -> bytes:
    """Capture a single JPEG frame from the webcam, return the bytes."""
    # Use a unique path so concurrent captures don't collide; also lets
    # us avoid scrot's overwrite footgun.
    path = f"/tmp/jarvis-cam-{os.getpid()}-{time.time_ns()}.jpg"
    try:
        subprocess.run(
            [
                "fswebcam",
                "-d", WEBCAM_DEVICE,
                "-r", WEBCAM_RESOLUTION,
                "--no-banner",
                "-q",
                "--jpeg", "85",
                path,
            ],
            check=True,
            timeout=10,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        with open(path, "rb") as f:
            return f.read()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


# ── Session safety guards ─────────────────────────────────────────────


def _check_guards() -> None:
    """Raise ComputerUseError if a safety limit is exceeded."""
    if _active_session is None:
        return
    # Tray kill switch — wins over everything else.
    if os.path.exists(_STOP_SIGNAL_FILE):
        try:
            os.unlink(_STOP_SIGNAL_FILE)
        except OSError:
            pass
        raise ComputerUseError(
            "Stopping: user clicked 'Stop Computer Use' in the tray. "
            "Tell the user the session was halted at their request."
        )
    if _active_session.consecutive_failures >= _FAILURE_LIMIT:
        raise ComputerUseError(
            f"Stopping after {_FAILURE_LIMIT} consecutive failures. "
            "The computer is not responding. Tell the user what you tried."
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
    if _active_session is not None:
        _active_session.consecutive_failures += 1


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
    """Format tool return value as a readable string for the LLM."""
    parts = [f"success={success}"]
    for k, v in kv.items():
        parts.append(f"{k}={v!r}")
    return ", ".join(parts)


# ── @function_tool implementations ───────────────────────────────────


@function_tool
async def computer_use(task: str) -> str:
    """Start a computer-use session to control the desktop visually.

    Call this when the user wants JARVIS to operate the computer — click
    buttons, type into fields, navigate apps. Gemini Vision describes the
    screen after each action; you (the LLM) decide the next click/type.
    Call computer_stop when the task is done.

    Only one session can run at a time.

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
    summary of the task that was attempted.
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
    for _ in range(max(1, int(count))):
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
                one wheel click (≈ 3 lines of text).
    """
    if _active_session is None:
        return "(no active computer-use session; call computer_use first)"
    try:
        _check_guards()
    except ComputerUseError as e:
        return _fmt_result(False, error=str(e))

    await _xdotool("mousemove", "--sync", str(x), str(y))
    btn = "5" if amount > 0 else "4"
    for _ in range(abs(int(amount))):
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
              "Return", "Escape", "ctrl+shift+n". Multiple keys joined with "+".
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
    that take time to render (page loads, animations, dialogs opening).

    Args:
        ms: Milliseconds to wait (default 500, clamped to 100..10000).
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
    "what's on the screen right now?" questions, or to orient yourself
    before starting a computer_use session.
    """
    try:
        desc = await _screenshot_and_describe()
        logger.info("[computer-use] one-shot screenshot")
        return desc
    except Exception as e:
        return f"(screenshot failed: {e})"


@function_tool
async def webcam_capture(prompt: str = "") -> str:
    """Capture a frame from the webcam and return a Gemini description.

    Use when the user asks what JARVIS sees, who's in the room, what
    they look like, what they're wearing, what's on their face, etc.
    Does NOT require an active computer_use session.

    Args:
        prompt: Optional override for the description focus
                (e.g. "is the user smiling?"). Empty = default
                "describe people + room" prompt.
    """
    try:
        loop = asyncio.get_running_loop()
        frame = await loop.run_in_executor(None, _take_webcam_frame)
        desc = await _gemini_describe(
            frame,
            mime_type="image/jpeg",
            prompt=prompt.strip() or WEBCAM_PROMPT,
        )
        logger.info(f"[computer-use] webcam_capture ({len(frame)} bytes)")
        return desc
    except Exception as e:
        return f"(webcam_capture failed: {e})"


@function_tool
async def watch_screen(seconds: int = 10) -> str:
    """Sample the screen over a time window and describe what changed.

    Use for "what just happened on my screen?" / "watch this video for
    a few seconds and tell me what you saw" / "is anything updating?".
    Captures the start frame and the end frame, sends both to Gemini,
    returns a comparative description.

    Does NOT require an active computer_use session.

    Args:
        seconds: How long to wait between the two frames (1..60, default 10).
    """
    seconds = max(1, min(int(seconds), 60))
    try:
        first = _take_screenshot()
        await asyncio.sleep(seconds)
        last = _take_screenshot()
        # Send both frames in one Gemini call so the model can diff them
        from google.genai import types as genai_types
        client = _get_gemini_client()
        loop = asyncio.get_running_loop()

        def _call() -> str:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=[
                    genai_types.Part.from_bytes(data=first, mime_type="image/png"),
                    genai_types.Part.from_bytes(data=last, mime_type="image/png"),
                    f"These are two screenshots of the same display, "
                    f"taken {seconds} seconds apart. Describe what changed "
                    f"between them — new windows, content updates, animations, "
                    f"user actions visible in the diff. Be specific.",
                ],
            )
            return response.text or "(no description returned)"

        desc = await loop.run_in_executor(None, _call)
        logger.info(f"[computer-use] watch_screen({seconds}s)")
        return desc
    except Exception as e:
        return f"(watch_screen failed: {e})"
