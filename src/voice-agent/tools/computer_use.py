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
from pathlib import Path

from livekit.agents import function_tool

logger = logging.getLogger("jarvis.computer_use")

# Vision-backend primitives extracted to tools/_vision_backend.py 2026-05-10
# (Step 7 of the audit). Re-exported under legacy underscored names so the
# in-file callers (`_resolved_vision_backend`, `_gemini_describe`, etc.) +
# the `VISION_BACKEND` / `OLLAMA_*` / `GEMINI_MODEL` constants stay reachable
# via `from tools.computer_use import X` (no caller change).
from tools._vision_backend import (
    VISION_BACKEND,
    OLLAMA_VISION_MODEL,
    OLLAMA_URL,
    GEMINI_MODEL,
    ollama_reachable        as _ollama_reachable,
    resolved_vision_backend as _resolved_vision_backend,
    get_gemini_client       as _get_gemini_client,
    ollama_describe         as _ollama_describe,
    gemini_describe_raw     as _gemini_describe_raw,
    gemini_describe         as _gemini_describe,
)

# Default video device for webcam_capture. Resolution order (first wins):
#   1. ~/.jarvis/webcam-device  (written by tray Camera-source submenu)
#   2. JARVIS_WEBCAM_DEVICE env var
#   3. /dev/video0
WEBCAM_DEVICE_FILE = Path.home() / ".jarvis" / "webcam-device"
_WEBCAM_DEVICE_DEFAULT = os.environ.get("JARVIS_WEBCAM_DEVICE", "/dev/video0")
WEBCAM_RESOLUTION = os.environ.get("JARVIS_WEBCAM_RES", "1280x720")


def _current_webcam_device() -> str:
    """Read ~/.jarvis/webcam-device if present; fall back to env/default."""
    try:
        if WEBCAM_DEVICE_FILE.exists():
            v = WEBCAM_DEVICE_FILE.read_text(encoding="utf-8").strip()
            if v.startswith("/dev/video"):
                return v
    except Exception:
        pass
    return _WEBCAM_DEVICE_DEFAULT


# Backwards-compat: callers that imported WEBCAM_DEVICE statically still work,
# but new captures should call _current_webcam_device().
WEBCAM_DEVICE = _WEBCAM_DEVICE_DEFAULT
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
# Casual "what's on my screen" prompt — used by the one-shot screenshot()
# tool. No coordinates, no element list — just 1-2 sentences. The detailed
# prompt above adds 10-15s to Gemini latency because it produces a long
# structured response; this one returns in 1-3s.
GEMINI_QUICK_SCREEN_PROMPT = (
    "In one or two sentences, describe what's on this screen — what app "
    "is open, what the user appears to be doing. No coordinates, no "
    "element list. Speak naturally as if telling someone over the phone."
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


# Max edge length for screenshots sent to Gemini. 2560x1600 PNGs are
# ~400 KB and the upload dominates round-trip latency (~15s observed).
# Downscaling to 1280 max + JPEG at quality 75 cuts payload to ~60 KB
# without losing readable UI text. Gemini's vision encoder uses tiles
# either way — extra resolution past ~1024 is mostly wasted.
_SCREENSHOT_MAX_EDGE = int(os.environ.get("JARVIS_SCREENSHOT_MAX_EDGE", "1280"))
_SCREENSHOT_JPEG_QUALITY = int(os.environ.get("JARVIS_SCREENSHOT_JPEG_Q", "75"))


def _take_screenshot() -> tuple[bytes, str]:
    """Take a screenshot, downscale + JPEG-encode, return (bytes, mime_type)."""
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
        # Downscale + re-encode as JPEG to shrink the upload.
        from PIL import Image
        import io
        with Image.open(path) as im:
            im = im.convert("RGB")
            w, h = im.size
            scale = min(1.0, _SCREENSHOT_MAX_EDGE / max(w, h))
            if scale < 1.0:
                im = im.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=_SCREENSHOT_JPEG_QUALITY, optimize=True)
            return buf.getvalue(), "image/jpeg"
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


async def _screenshot_and_describe() -> str:
    """Take screenshot, send to Gemini, return description.

    Logs per-stage timing so latency regressions are visible.
    """
    t0 = time.monotonic()
    img_bytes, mime = _take_screenshot()
    t_capture = time.monotonic() - t0
    t1 = time.monotonic()
    desc = await _gemini_describe(img_bytes, mime_type=mime)
    t_gemini = time.monotonic() - t1
    logger.info(
        f"[computer-use] screenshot+describe: capture={t_capture*1000:.0f}ms "
        f"gemini={t_gemini*1000:.0f}ms img={len(img_bytes)/1024:.0f}KB ({mime})"
    )
    return desc


def _take_webcam_frame() -> bytes:
    """Capture a single JPEG frame from the webcam, return the bytes."""
    # Use a unique path so concurrent captures don't collide; also lets
    # us avoid scrot's overwrite footgun.
    path = f"/tmp/jarvis-cam-{os.getpid()}-{time.time_ns()}.jpg"
    device = _current_webcam_device()
    try:
        subprocess.run(
            [
                "fswebcam",
                "-d", device,
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


async def _gemini_live_describe(
    duration_s: int,
    instruction: str,
    frame_interval_s: float = 1.5,
) -> str:
    """Open a Gemini Live websocket session, stream screenshot frames
    for duration_s seconds, return the concatenated text response.

    Uses gemini-3.1-flash-live-preview (matching the Google AI Studio
    sample). Returns text-only output (audio modality not used because
    JARVIS has its own TTS pipeline). Raises ComputerUseError on quota
    exhaustion (1011) — Live API needs paid Gemini billing.
    """
    from google import genai
    from google.genai import types as genai_types
    key = os.environ.get("GOOGLE_API_KEY", "")
    if not key:
        raise ComputerUseError("GOOGLE_API_KEY not set")

    client = genai.Client(api_key=key)
    config = genai_types.LiveConnectConfig(
        response_modalities=["TEXT"],
        media_resolution=genai_types.MediaResolution.MEDIA_RESOLUTION_MEDIUM,
        context_window_compression=genai_types.ContextWindowCompressionConfig(
            trigger_tokens=104857,
            sliding_window=genai_types.SlidingWindow(target_tokens=52428),
        ),
        system_instruction=genai_types.Content(
            parts=[genai_types.Part(text=(
                "You are JARVIS's eyes via continuous screen share. "
                "Describe what changes between frames as a stream of "
                "short observations. No preamble, no closer."
            ))],
        ),
    )

    chunks: list[str] = []
    try:
        async with client.aio.live.connect(
            model="models/gemini-3.1-flash-live-preview",
            config=config,
        ) as session:
            # Send the user's instruction as the opening turn
            await session.send_client_content(
                turns=[genai_types.Content(
                    role="user",
                    parts=[genai_types.Part(text=instruction)],
                )],
                turn_complete=False,  # we'll keep the turn open with frames
            )

            # Background task: capture and send screen frames
            stop_at = time.monotonic() + duration_s
            async def _stream_frames():
                while time.monotonic() < stop_at:
                    img_bytes, mime = _take_screenshot()
                    await session.send_realtime_input(
                        video=genai_types.Blob(data=img_bytes, mime_type=mime),
                    )
                    await asyncio.sleep(frame_interval_s)

            frame_task = asyncio.create_task(_stream_frames())
            try:
                async for msg in session.receive():
                    if msg.text:
                        chunks.append(msg.text)
                    sc = getattr(msg, "server_content", None)
                    if sc and sc.model_turn:
                        for part in sc.model_turn.parts or []:
                            if part.text:
                                chunks.append(part.text)
                    if time.monotonic() >= stop_at:
                        break
            finally:
                frame_task.cancel()
                try:
                    await frame_task
                except asyncio.CancelledError:
                    pass
    except Exception as e:
        msg = str(e)
        if "1011" in msg or "RESOURCE_EXHAUSTED" in msg or "quota" in msg.lower():
            raise ComputerUseError(
                "Gemini Live API needs paid billing on this project. "
                "Free tier returns 1011 quota errors immediately. "
                "Enable billing at console.cloud.google.com → APIs & Services → Billing, "
                "then this tool will work."
            ) from e
        raise

    return "".join(chunks).strip() or "(no description from Live session)"


async def _live_screen_polling(
    duration_s: int,
    interval_s: float = 2.0,
    on_frame=None,
):
    """Free-tier streaming: poll screenshot+describe every interval_s.

    Each frame's description is yielded via on_frame callback (if given)
    AND collected into the final return string. Uses Gemini Flash
    one-shot calls — works on the free tier. No Live API needed.

    The `focus` is baked into a per-frame prompt that emphasizes brevity
    and only-what-changed.
    """
    POLL_PROMPT = (
        "In ONE short sentence, what is happening on this screen right "
        "now? No preamble, no closer, no 'the screen shows'. Just the "
        "key state or change."
    )
    descriptions = []
    last_desc = ""
    stop_at = time.monotonic() + duration_s
    while time.monotonic() < stop_at:
        try:
            img_bytes, mime = _take_screenshot()
            desc = await _gemini_describe(
                img_bytes, mime_type=mime, prompt=POLL_PROMPT,
            )
            desc = desc.strip()
            # Skip frames where the description is a duplicate — saves
            # voicing redundant lines when the screen hasn't changed.
            if desc and desc != last_desc:
                descriptions.append(desc)
                last_desc = desc
                if on_frame is not None:
                    try:
                        result = on_frame(desc)
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception as e:
                        logger.warning(f"[live_screen] on_frame error: {e}")
        except Exception as e:
            logger.warning(f"[live_screen] frame failed: {e}")
        # Wait until next poll, but don't overshoot the stop time.
        remaining = stop_at - time.monotonic()
        if remaining > 0:
            await asyncio.sleep(min(interval_s, remaining))
    return "\n".join(descriptions)


@function_tool
async def live_screen(duration_s: int = 10, focus: str = "") -> str:
    """Stream screen observations for N seconds via polling Gemini Flash.

    Free-tier compatible — captures a screenshot every ~2 seconds, asks
    Gemini Flash for a one-sentence description, returns the joined
    descriptions. Works on the free Google API tier (no Live API,
    no paid billing required).

    For one-shot "what's on my screen right now", use screenshot()
    instead — single frame, one Gemini call.

    Args:
        duration_s: How long to stream (1..60, default 10).
        focus:      Currently unused; the polling prompt is fixed for
                    brevity. Kept for API compat with prior signature.
    """
    duration_s = max(1, min(int(duration_s), 60))
    try:
        t0 = time.monotonic()
        desc = await _live_screen_polling(duration_s)
        elapsed = time.monotonic() - t0
        logger.info(
            f"[computer-use] live_screen({duration_s}s polling) → "
            f"{len(desc)} chars in {elapsed:.1f}s"
        )
        return desc or "(nothing visible changed during the session)"
    except Exception as e:
        return f"(live_screen failed: {e})"


@function_tool
async def screenshot() -> str:
    """Take a screenshot and return a brief Gemini description of the screen.

    Does NOT require an active computer_use session — use this for one-off
    "what's on the screen right now?" voice questions. Returns 1-2 sentences
    suitable for speaking aloud (no coordinates, no UI element list).

    For computer-use action loops where coordinates are needed, the
    computer_use → click/type tools use the detailed prompt automatically.
    """
    try:
        t0 = time.monotonic()
        img_bytes, mime = _take_screenshot()
        t_capture = time.monotonic() - t0
        t1 = time.monotonic()
        desc = await _gemini_describe(
            img_bytes,
            mime_type=mime,
            prompt=GEMINI_QUICK_SCREEN_PROMPT,
        )
        t_gemini = time.monotonic() - t1
        logger.info(
            f"[computer-use] one-shot screenshot: capture={t_capture*1000:.0f}ms "
            f"gemini={t_gemini*1000:.0f}ms img={len(img_bytes)/1024:.0f}KB"
        )
        return desc
    except Exception as e:
        return f"(screenshot failed: {e})"


# Face ID — extracted to tools/face_id.py 2026-05-10 (Step 7 of the
# audit). Re-exported below so the existing jarvis_agent imports
# (`from tools.computer_use import face_register, ...`) stay working.
from tools.face_id import (
    FACES_DIR,
    FACE_THRESHOLD,
    FACE_ENROLL_FRAMES,
    FACE_LIVENESS_FRAMES,
    IR_DEVICE,
    face_register,
    face_identify,
    face_list,
    face_delete,
)


@function_tool
async def webcam_capture(prompt: str | None = None) -> str:
    """Capture a frame from the webcam and return a Gemini description.

    Use when the user asks what JARVIS sees, who's in the room, what
    they look like, what they're wearing, what's on their face, etc.
    Does NOT require an active computer_use session.

    Args:
        prompt: Optional override for the description focus
                (e.g. "is the user smiling?"). Omit / null for default
                "describe people + room" prompt.
    """
    # Signature note: `str | None = None` rather than `str = ""` so the
    # JSON-Schema generated for tool-call validation marks `prompt` as
    # nullable rather than as a required string. Groq's tool-call
    # validator treated the prior `str = ""` schema as required, which
    # caused 4xx APIErrors when the LLM omitted the field — those errors
    # cascaded into 4× retry storms (~10s of silence per turn) and were
    # the actual reason the user had to ask questions twice.
    p = (prompt or "").strip() or WEBCAM_PROMPT
    try:
        loop = asyncio.get_running_loop()
        frame = await loop.run_in_executor(None, _take_webcam_frame)
        desc = await _gemini_describe(
            frame,
            mime_type="image/jpeg",
            prompt=p,
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
        first, mime = _take_screenshot()
        await asyncio.sleep(seconds)
        last, _ = _take_screenshot()
        # Send both frames in one Gemini call so the model can diff them
        from google.genai import types as genai_types
        client = _get_gemini_client()
        loop = asyncio.get_running_loop()

        def _call() -> str:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=[
                    genai_types.Part.from_bytes(data=first, mime_type=mime),
                    genai_types.Part.from_bytes(data=last, mime_type=mime),
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
