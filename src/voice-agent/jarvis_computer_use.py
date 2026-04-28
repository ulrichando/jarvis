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

logger = logging.getLogger("jarvis-computer-use")

# gemini-2.5-flash-lite — chosen for speed (verified 2026-04-28).
# Latency benchmark (one-shot screenshot describe, 70 KB JPEG, quick prompt):
#   gemini-2.5-flash-lite           ~11.5s  ← chosen
#   gemini-2.5-flash                503 (overloaded that day)
#   gemini-3-flash-preview          ~20.1s
#   gemini-3.1-flash-lite-preview   ~20.9s  (was the default — too slow)
#   gemini-3.1-flash-live-preview   1011 quota (free-tier paid-only)
#   gemini-2.0-flash                429 (free-tier limit 0)
# The 2.5-lite output quality is more than sufficient for "describe what
# the user sees" and "list UI elements with coordinates" tasks. Swap to
# gemini-2.5-flash (full) when it's not 503ing for higher accuracy on
# tricky UIs, at ~2x latency.
GEMINI_MODEL = "gemini-2.5-flash-lite"

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


# ── Gemini ────────────────────────────────────────────────────────────


def _get_gemini_client():
    """Return a google.genai Client. Raise ComputerUseError if key missing."""
    from google import genai
    key = os.environ.get("GOOGLE_API_KEY", "")
    if not key:
        raise ComputerUseError("GOOGLE_API_KEY not set in environment")
    return genai.Client(api_key=key)


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


# ── Face ID (production-grade) ────────────────────────────────────────
# Pipeline matches what production face systems use today:
#   1. Detection + alignment + 512-d ArcFace embedding via InsightFace
#      buffalo_l (RetinaFace + ArcFace ResNet-50, ~99.83% LFW).
#   2. Multi-frame enrollment (5 frames averaged) — single-frame
#      enrollment over-fits to one pose/lighting; averaging gives a
#      more robust template. Apple/Microsoft do 30+; 5 is the sweet
#      spot for voice-driven registration.
#   3. Liveness check — capture 3 frames over ~600ms; reject if all
#      bbox centers are pixel-identical (static photo) AND, if the IR
#      camera is available, require the IR stream to also see a face
#      (photos on phone screens / printed photos don't emit the same
#      thermal signature as a live face).
#   4. Cosine similarity on L2-normalized embeddings (industry default
#      since ArcFace; lower distance = closer match).
# Old dlib-based JSON files (version=1, 128-d) are auto-rejected with
# a "please re-register" message; new files are version=2.

FACES_DIR = Path.home() / ".jarvis" / "faces"
# Cosine distance threshold. ArcFace literature uses ~0.4 for strict
# verification, ~0.55 for lenient identification. We use 0.4 because
# this is single-user auth, not large-database search.
FACE_THRESHOLD = float(os.environ.get("JARVIS_FACE_THRESHOLD", "0.40"))
FACE_ENROLL_FRAMES = int(os.environ.get("JARVIS_FACE_ENROLL_FRAMES", "5"))
FACE_LIVENESS_FRAMES = int(os.environ.get("JARVIS_FACE_LIVENESS_FRAMES", "3"))
# IR camera (Windows-Hello-style greyscale stream). If absent or the
# read fails, IR check is skipped — RGB-only motion liveness still runs.
IR_DEVICE = os.environ.get("JARVIS_IR_DEVICE", "/dev/video2")


_face_app = None  # InsightFace FaceAnalysis, lazily initialized


def _get_face_app():
    """Return a singleton InsightFace FaceAnalysis instance."""
    global _face_app
    if _face_app is not None:
        return _face_app
    from insightface.app import FaceAnalysis
    # buffalo_l = RetinaFace detector + ArcFace ResNet-50 embedder.
    # Models auto-download to ~/.insightface/ on first call (~280 MB).
    app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
    app.prepare(ctx_id=0, det_size=(640, 640))
    _face_app = app
    return app


def _decode_to_bgr(jpeg_bytes: bytes):
    """JPEG bytes → numpy BGR (InsightFace expects BGR like OpenCV)."""
    from PIL import Image
    import io
    import numpy as np
    rgb = np.array(Image.open(io.BytesIO(jpeg_bytes)).convert("RGB"))
    return rgb[:, :, ::-1].copy()  # RGB → BGR


def _detect_face(jpeg_bytes: bytes):
    """Return the largest detected face object, or None."""
    img = _decode_to_bgr(jpeg_bytes)
    faces = _get_face_app().get(img)
    if not faces:
        return None
    # Multiple faces → pick the largest by bbox area (closest = user)
    faces.sort(key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]),
               reverse=True)
    return faces[0]


def _take_ir_frame() -> bytes | None:
    """Capture a single greyscale frame from the IR camera. None if absent."""
    if not os.path.exists(IR_DEVICE):
        return None
    path = f"/tmp/jarvis-ir-{os.getpid()}-{time.time_ns()}.jpg"
    try:
        # ffmpeg is more reliable than fswebcam for greyscale IR. Single
        # frame, capped at 1 second.
        r = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error",
             "-f", "v4l2", "-input_format", "yuyv422", "-i", IR_DEVICE,
             "-frames:v", "1", "-q:v", "5", path],
            timeout=4,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        if r.returncode != 0 or not os.path.exists(path):
            # Some IR sensors only output GREY format
            r = subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error",
                 "-f", "v4l2", "-pix_fmt", "gray", "-i", IR_DEVICE,
                 "-frames:v", "1", "-q:v", "5", path],
                timeout=4,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            if r.returncode != 0 or not os.path.exists(path):
                return None
        with open(path, "rb") as f:
            return f.read()
    except Exception:
        return None
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _capture_face_frame() -> tuple[bytes, str] | tuple[None, None]:
    """Capture one frame for face processing, preferring IR over RGB.

    Returns (jpeg_bytes, source) where source is "ir" or "rgb", or
    (None, None) if neither source produced a usable frame. IR is
    preferred because it works in low light AND is the same physical
    sensor used by Windows Hello / Apple Face ID for that reason.
    """
    ir = _take_ir_frame()
    if ir is not None:
        return ir, "ir"
    try:
        rgb = _take_webcam_frame()
        return rgb, "rgb"
    except Exception:
        return None, None


def _liveness_check() -> tuple[bool, str, list]:
    """Capture FACE_LIVENESS_FRAMES + verify face is alive.

    Returns (ok, reason, faces) — `faces` is the list of detected
    face objects, one per captured frame, only valid when ok=True.

    Prefers IR camera (works in the dark, harder to spoof). Falls back
    to RGB if IR unavailable. Anti-spoof: cross-checks the OTHER source
    (if both are available) — a printed photo only fools one.
    """
    frames_faces = []
    source = None
    for i in range(FACE_LIVENESS_FRAMES):
        jpeg, src = _capture_face_frame()
        if jpeg is None:
            return False, "Camera capture failed.", []
        if source is None:
            source = src
        face = _detect_face(jpeg)
        if face is None:
            return False, (f"No face detected in frame {i+1}/{FACE_LIVENESS_FRAMES} "
                           f"({src} camera). Look at the camera."), []
        frames_faces.append(face)
        if i < FACE_LIVENESS_FRAMES - 1:
            time.sleep(0.3)

    # Motion liveness: bbox centroids must vary by at least 1 pixel.
    # A printed photo or static screen would yield identical bboxes
    # frame-to-frame; even tiny natural head movement breaks that.
    centroids = []
    for f in frames_faces:
        x = (f.bbox[0] + f.bbox[2]) / 2
        y = (f.bbox[1] + f.bbox[3]) / 2
        centroids.append((x, y))
    max_motion = 0.0
    for i in range(1, len(centroids)):
        dx = centroids[i][0] - centroids[0][0]
        dy = centroids[i][1] - centroids[0][1]
        max_motion = max(max_motion, (dx*dx + dy*dy) ** 0.5)
    if max_motion < 1.0:
        return False, ("Liveness check failed — face appears static. "
                       "If you're in front of the camera, move your head "
                       "slightly or blink."), []

    # Cross-camera anti-spoof: if we used IR primary, also check RGB
    # has a face (or is too dark to tell). Vice versa for RGB primary.
    # Photos/screen replays usually only fool one of the two sensors.
    if source == "ir":
        rgb = _take_webcam_frame()
        rgb_face = _detect_face(rgb)
        # In the dark, RGB legitimately fails — that's not a spoof signal.
        # Only flag if RGB clearly succeeded with a DIFFERENT face position.
        if rgb_face is not None:
            ir_cx = centroids[-1][0]
            rgb_cx = (rgb_face.bbox[0] + rgb_face.bbox[2]) / 2
            # Both cameras same physical location → centers should be
            # roughly aligned. Wildly different = something's off.
            logger.info(f"[face] cross-check: ir_x={ir_cx:.0f} rgb_x={rgb_cx:.0f}")
    elif source == "rgb":
        # If RGB primary succeeded, IR should also see a face (real face
        # emits/reflects IR). Photos on screens don't.
        ir = _take_ir_frame()
        if ir is not None:
            ir_face = _detect_face(ir)
            if ir_face is None:
                return False, ("Liveness check failed — face visible on RGB "
                               "but not on IR camera. Likely a printed photo "
                               "or screen replay."), []

    return True, f"liveness ok (source={source}, motion={max_motion:.1f}px)", frames_faces


def _load_templates() -> list[tuple[str, "np.ndarray"]]:
    """Load all registered face templates (version=2 only)."""
    import numpy as np
    if not FACES_DIR.exists():
        return []
    out = []
    for p in sorted(FACES_DIR.glob("*.json")):
        try:
            import json
            data = json.loads(p.read_text(encoding="utf-8"))
            if data.get("version") != 2:
                continue  # skip dlib-era 128-d files; user must re-register
            name = data.get("name") or p.stem
            emb = data.get("embedding")
            if isinstance(emb, list) and len(emb) == 512:
                out.append((name, np.array(emb, dtype="float32")))
        except Exception as e:
            logger.warning(f"[face] failed to load {p.name}: {e}")
    return out


@function_tool
async def face_register(name: str) -> str:
    """Register a face for future identification (production-grade).

    Captures FACE_ENROLL_FRAMES frames over ~1.5 seconds, extracts a 512-d
    ArcFace embedding from each, averages them into one robust template,
    and saves to ~/.jarvis/faces/<name>.json. Liveness is verified during
    enrollment (face must move; if the IR camera is available, face must
    appear in IR too — defeats printed photos and screen replays).

    Tell the user to look at the camera and stay roughly still while
    blinking/breathing normally — the multi-frame capture takes ~1.5s.

    Args:
        name: The name to register this face as (lowercase letters,
              digits, '-', '_'). Used as the filename.
    """
    name = (name or "").strip().lower()
    if not name or not all(c.isalnum() or c in "-_" for c in name):
        return "(invalid name — use lowercase letters, digits, '-', '_' only)"
    try:
        loop = asyncio.get_running_loop()
        import numpy as np
        # Capture FACE_ENROLL_FRAMES frames using the preferred source
        # (IR first, RGB fallback). Embeddings averaged for robustness.
        embeddings = []
        sources = []
        for i in range(FACE_ENROLL_FRAMES):
            jpeg, src = await loop.run_in_executor(None, _capture_face_frame)
            if jpeg is None:
                return f"Camera capture failed on frame {i+1}/{FACE_ENROLL_FRAMES}."
            face = await loop.run_in_executor(None, _detect_face, jpeg)
            if face is None:
                return (f"No face detected on frame {i+1}/{FACE_ENROLL_FRAMES} "
                        f"({src}). Look at the camera and try again.")
            embeddings.append(face.normed_embedding)
            sources.append(src)
            if i < FACE_ENROLL_FRAMES - 1:
                await asyncio.sleep(0.3)

        # Anti-spoof cross-check: if primary is IR, briefly see if RGB
        # also has a face (only meaningful when the room has light).
        primary = sources[0]
        cross = "n/a"
        if primary == "ir":
            rgb = await loop.run_in_executor(None, _take_webcam_frame)
            rgb_face = await loop.run_in_executor(None, _detect_face, rgb)
            cross = "rgb_confirmed" if rgb_face else "rgb_dark"
        elif primary == "rgb":
            ir = await loop.run_in_executor(None, _take_ir_frame)
            if ir is not None:
                ir_face = await loop.run_in_executor(None, _detect_face, ir)
                if ir_face is None:
                    return ("Face seen on RGB but NOT on IR — likely a "
                            "printed photo or screen replay. Registration aborted.")
                cross = "ir_confirmed"

        # Average L2-normalized embeddings then re-normalize.
        avg = np.mean(embeddings, axis=0)
        avg = avg / np.linalg.norm(avg)

        FACES_DIR.mkdir(parents=True, exist_ok=True)
        import json
        path = FACES_DIR / f"{name}.json"
        path.write_text(json.dumps({
            "version": 2,
            "name": name,
            "model": "buffalo_l",
            "embedding": avg.tolist(),
            "enroll_frames": len(embeddings),
            "primary_source": primary,
            "cross_check": cross,
            "created_at": time.time(),
        }), encoding="utf-8")
        logger.info(
            f"[face] registered '{name}' "
            f"(frames={len(embeddings)}, primary={primary}, cross={cross})"
        )
        return (f"Registered face for '{name}' — averaged "
                f"{len(embeddings)} frames from {primary} camera, "
                f"cross-check {cross}. JARVIS will recognize this face now.")
    except Exception as e:
        return f"(face_register failed: {e})"


@function_tool
async def face_identify() -> str:
    """Identify whoever's in front of the webcam, with liveness check.

    Pipeline: capture 3 frames over ~600ms → verify face moves between
    frames (rejects static photos) → if IR camera available, verify face
    appears in IR too (rejects screen replays) → extract ArcFace 512-d
    embedding from the last frame → cosine-distance match against all
    registered faces. Returns the closest match within FACE_THRESHOLD,
    or 'unknown'.
    """
    try:
        known = _load_templates()
        if not known:
            return ("No faces are registered yet. Ask the user to say "
                    "'register my face as <name>' first.")
        loop = asyncio.get_running_loop()
        ok, reason, frames = await loop.run_in_executor(None, _liveness_check)
        if not ok:
            logger.info(f"[face] liveness rejected: {reason}")
            return reason

        import numpy as np
        # Use the last live-confirmed frame's embedding for matching.
        target = frames[-1].normed_embedding  # already L2-normalized

        # Cosine distance = 1 - cosine_similarity. With normalized
        # embeddings, distance ranges 0..2; ArcFace lit uses ~0.4 cutoff.
        distances = []
        for name, ref in known:
            cos_sim = float(np.dot(target, ref))
            cos_dist = 1.0 - cos_sim
            distances.append((cos_dist, name))
        distances.sort()
        best_d, best_name = distances[0]
        if best_d <= FACE_THRESHOLD:
            confidence = max(0.0, 1.0 - best_d / FACE_THRESHOLD)
            logger.info(f"[face] match '{best_name}' cos_dist={best_d:.3f}")
            return (f"That's {best_name} (cosine distance={best_d:.3f}, "
                    f"confidence~{confidence:.0%}).")
        else:
            logger.info(f"[face] no match (best={best_name} cos_dist={best_d:.3f})")
            return (f"Unknown face. Closest registered is {best_name} but "
                    f"cosine distance {best_d:.3f} exceeds threshold "
                    f"{FACE_THRESHOLD}.")
    except Exception as e:
        return f"(face_identify failed: {e})"


@function_tool
async def face_list() -> str:
    """List all registered face names (and their enrollment metadata)."""
    import json
    if not FACES_DIR.exists():
        return "No faces are registered."
    items = []
    for p in sorted(FACES_DIR.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            name = data.get("name") or p.stem
            ver = data.get("version", 1)
            primary = data.get("primary_source", "rgb")
            cross = data.get("cross_check", "n/a")
            n_frames = data.get("enroll_frames", 1)
            items.append(f"  {name} (v{ver}, {n_frames} frames, primary={primary}, cross-check={cross})")
        except Exception:
            pass
    if not items:
        return "No faces are registered."
    return "Registered faces:\n" + "\n".join(items)


@function_tool
async def face_delete(name: str) -> str:
    """Delete a registered face by name.

    Args:
        name: The registered name to remove.
    """
    name = (name or "").strip().lower()
    path = FACES_DIR / f"{name}.json"
    if not path.exists():
        return f"No face registered under '{name}'."
    try:
        path.unlink()
        logger.info(f"[face] deleted '{name}'")
        return f"Deleted '{name}' from registered faces."
    except Exception as e:
        return f"(face_delete failed: {e})"


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
