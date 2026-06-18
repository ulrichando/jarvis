"""On-demand webcam frame capture for LLM vision.

Two frame sources, tried in order:

1. **Person-tracker frame file** (``~/.jarvis/person_tracker.jpg``) — when the
   kiosk person tracker (:mod:`vision.person_tracker`, opt-in via
   ``JARVIS_PERSON_TRACKER=1`` in the voice-client process) is running, it
   holds the V4L2 device open and writes the latest frame to disk at ~5 Hz.
   A fresh file (mtime within ``TRACKER_FRESH_S``) is used directly — opening
   the device a second time while the tracker streams from it fails with
   EBUSY on most V4L2 drivers.
2. **Direct cv2 capture** — open the device, discard a few warm-up frames
   (cold sensors need several reads before auto-exposure settles), JPEG-encode
   the last one, release. Open-per-call, serialized by a module lock. The IR
   dark-assist capture keeps the brightest burst frame instead — the IR
   emitter strobes on alternating frames, so the last frame is often unlit.

Hardware detection: by default the camera is AUTO-DETECTED — V4L2 nodes are
enumerated from ``/dev/video*`` (:func:`detect_webcam_devices`) and capture
tries each in order, skipping non-capture nodes (UVC metadata siblings).
Explicit pins win over auto-detection:

  JARVIS_WEBCAM_DEVICE        — pin availability-gating to one device node
                                (unset = any enumerated node counts)
  JARVIS_WEBCAM_INDEX         — pin capture to one cv2 index
                                (unset = try enumerated nodes in order)
  JARVIS_WEBCAM_RES           — capture WxH (default: 640x480)
  JARVIS_TRACKER_FRAME_FILE   — tracker frame path
                                (default: ~/.jarvis/person_tracker.jpg)

cv2 is imported lazily so this module stays importable on hosts without
OpenCV (the tool layer gates on :func:`webcam_available` instead).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Tuple

log = logging.getLogger(__name__)

# Tracker writes the frame file at ~5 Hz; anything older than this means the
# tracker is stopped (or wedged) and the device is presumably free to open.
TRACKER_FRESH_S = 2.0

# Frames discarded after a cold open before the kept frame, so auto-exposure
# and auto-white-balance have settled.
WARMUP_FRAMES = 4

JPEG_QUALITY = 80
MAX_DIM = 1024  # longest edge after resize; keeps vision-API payloads small

_capture_lock = threading.Lock()


class WebcamError(RuntimeError):
    """Raised when no frame could be captured from any source."""


def _pinned_device_path() -> str | None:
    """Explicit JARVIS_WEBCAM_DEVICE pin, or None to auto-detect."""
    return os.environ.get("JARVIS_WEBCAM_DEVICE") or None


def _pinned_index() -> int | None:
    """Explicit JARVIS_WEBCAM_INDEX pin, or None to auto-detect."""
    raw = os.environ.get("JARVIS_WEBCAM_INDEX")
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def detect_webcam_devices() -> list[str]:
    """Hardware detection: enumerate V4L2 video nodes present on this machine.

    Returns existing ``/dev/video<N>`` character devices sorted by index.
    Presence ≠ capture-capable (UVC cams expose a metadata node alongside the
    capture node), so the capture path tries indexes in order until one
    yields frames.
    """
    import glob
    import stat

    nodes: list[tuple[int, str]] = []
    for path in glob.glob("/dev/video[0-9]*"):
        suffix = path[len("/dev/video"):]
        if not suffix.isdigit():
            continue
        try:
            if stat.S_ISCHR(os.stat(path).st_mode):
                nodes.append((int(suffix), path))
        except OSError:
            continue
    return [path for _, path in sorted(nodes)]


def device_name(index: int) -> str:
    """Kernel-reported hardware name for /dev/video<index> (sysfs), or ''."""
    try:
        return (
            Path(f"/sys/class/video4linux/video{index}/name")
            .read_text()
            .strip()
        )
    except OSError:
        return ""


def _ir_index() -> int | None:
    """Index of the IR (Windows-Hello-style) sensor, or None when disabled.

    Defaults from JARVIS_IR_DEVICE (/dev/video2 — same default as
    pipeline/config.py); JARVIS_IR_INDEX overrides directly. The node must
    actually exist to count.
    """
    raw = os.environ.get("JARVIS_IR_INDEX")
    if raw:
        try:
            idx = int(raw)
        except ValueError:
            return None
    else:
        dev = os.environ.get("JARVIS_IR_DEVICE", "/dev/video2")
        suffix = dev[len("/dev/video"):] if dev.startswith("/dev/video") else ""
        if not suffix.isdigit():
            return None
        idx = int(suffix)
    return idx if os.path.exists(f"/dev/video{idx}") else None


def _candidate_indexes() -> list[int]:
    """RGB capture indexes to try, in order. Explicit pin wins; else hardware
    scan — with the IR sensor excluded so a failing RGB cam can't silently
    fall back to greyscale infrared (IR is used deliberately, via dark-frame
    assist in grab_jpeg)."""
    pinned = _pinned_index()
    if pinned is not None:
        return [pinned]
    ir = _ir_index()
    detected = [
        idx
        for p in detect_webcam_devices()
        if (idx := int(p[len("/dev/video"):])) != ir
    ]
    return detected or [0]


def _capture_res() -> Tuple[int, int]:
    raw = os.environ.get("JARVIS_WEBCAM_RES", "640x480")
    try:
        w, h = raw.split("x")
        return int(w), int(h)
    except ValueError:
        return 640, 480


def _tracker_frame_path() -> Path:
    return Path(
        os.environ.get(
            "JARVIS_TRACKER_FRAME_FILE",
            str(Path.home() / ".jarvis" / "person_tracker.jpg"),
        )
    )


def _fresh_tracker_jpeg() -> bytes | None:
    """Return the tracker's frame bytes if the file is fresh, else None."""
    path = _tracker_frame_path()
    try:
        stat = path.stat()
    except OSError:
        return None
    if time.time() - stat.st_mtime > TRACKER_FRESH_S or stat.st_size == 0:
        return None
    try:
        return path.read_bytes()
    except OSError:
        return None


def webcam_available() -> bool:
    """Cheap availability probe for tool gating — no cv2 import, no device open.

    Hardware-aware: when JARVIS_WEBCAM_DEVICE is explicitly pinned, only that
    node counts (deterministic for tests + multi-cam setups); otherwise any
    enumerated V4L2 node counts. A fresh person-tracker frame also counts —
    the tracker demonstrably has a camera even if enumeration looks odd.
    """
    pinned = _pinned_device_path()
    if pinned is not None:
        if os.path.exists(pinned):
            return True
    elif detect_webcam_devices():
        return True
    return _fresh_tracker_jpeg() is not None


def resize_and_encode_jpeg(frame, max_dim: int = MAX_DIM, quality: int = JPEG_QUALITY) -> bytes:
    """Downscale a BGR frame so its longest edge is <= max_dim, JPEG-encode it."""
    import cv2

    h, w = frame.shape[:2]
    longest = max(w, h)
    if longest > max_dim:
        scale = max_dim / float(longest)
        frame = cv2.resize(frame, (int(w * scale), int(h * scale)))
    ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise WebcamError("failed to encode webcam frame as JPEG")
    return jpeg.tobytes()


def _open_and_grab(index: int, pick_brightest: bool = False) -> bytes:
    """Open one camera index cold, warm it up, return a JPEG. Always releases.

    pick_brightest keeps the brightest frame of the burst instead of the last:
    Windows-Hello-style IR emitters strobe on alternating frames (measured on
    this hardware: luma 16/50/16/44/…), so "last frame" deterministically
    lands on an unlit one. RGB capture keeps the last frame — auto-exposure
    settles over the burst, so latest is best there.
    """
    try:
        import cv2
    except ImportError as exc:
        raise WebcamError(f"OpenCV (cv2) is not installed: {exc}") from exc

    width, height = _capture_res()
    cap = cv2.VideoCapture(index)
    try:
        if not cap.isOpened():
            raise WebcamError(
                f"cannot open webcam index {index} — node missing, not a capture "
                "device, or held by another process"
            )
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)

        frame = None
        best_luma = -1.0
        for _ in range(WARMUP_FRAMES + 1):
            ok, candidate = cap.read()
            if not ok or candidate is None:
                continue
            if pick_brightest:
                luma = float(candidate.mean())
                if luma > best_luma:
                    frame, best_luma = candidate, luma
            else:
                frame = candidate
        if frame is None:
            raise WebcamError(f"webcam index {index} opened but returned no frames")
        return resize_and_encode_jpeg(frame)
    finally:
        cap.release()


def _capture_device_jpeg() -> bytes:
    """Capture from the first working camera among the hardware candidates.

    Tries the pinned index when set, otherwise every enumerated V4L2 node in
    order — UVC metadata nodes (which open but yield no frames) are skipped
    by the per-index failure path.
    """
    candidates = _candidate_indexes()
    errors: list[str] = []
    for index in candidates:
        try:
            return _open_and_grab(index)
        except WebcamError as exc:
            name = device_name(index)
            errors.append(f"video{index}{f' ({name})' if name else ''}: {exc}")
    detected = detect_webcam_devices()
    raise WebcamError(
        "no camera hardware produced a frame. "
        + (f"Detected nodes: {', '.join(detected)}. " if detected else "No V4L2 nodes detected. ")
        + "; ".join(errors)
    )


def mean_jpeg_luma(jpeg: bytes) -> float:
    """Mean luminance (0–255) of an encoded frame; 255.0 on decode failure
    (fail-bright: never triggers the dark-assist path on bad data)."""
    try:
        import cv2
        import numpy as np

        frame = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        if frame is None:
            return 255.0
        return float(frame.mean())
    except Exception:
        return 255.0


def dark_luma_threshold() -> float:
    """Luma below which a frame counts as "dark" (JARVIS_WEBCAM_DARK_LUMA)."""
    try:
        return float(os.environ.get("JARVIS_WEBCAM_DARK_LUMA", "32"))
    except ValueError:
        return 32.0


def _ir_assist_enabled() -> bool:
    return os.environ.get("JARVIS_WEBCAM_IR_ASSIST", "1") == "1"


def _maybe_ir_assist(rgb_jpeg: bytes) -> Tuple[bytes, str] | None:
    """Dark-room assist: when the RGB frame is too dark to be useful, try the
    IR (Windows-Hello-style) sensor and use it if meaningfully brighter.

    The IR cam works in darkness (its emitter / ambient IR); frames are
    greyscale 576x360 — fine for scene description, deliberately NOT used
    for face recognition (SFace embeddings are RGB-trained; recognition
    callers pass ``grab_jpeg(allow_ir=False)``).
    """
    if not _ir_assist_enabled():
        return None
    ir = _ir_index()
    if ir is None:
        return None
    rgb_luma = mean_jpeg_luma(rgb_jpeg)
    if rgb_luma >= dark_luma_threshold():
        return None
    try:
        # Brightest-of-burst: the emitter strobes on alternating frames, and
        # an unlit frame here would make the assist wrongly reject IR as
        # "no better than RGB".
        ir_jpeg = _open_and_grab(ir, pick_brightest=True)
    except WebcamError as exc:
        log.debug("IR assist capture failed: %s", exc)
        return None
    ir_luma = mean_jpeg_luma(ir_jpeg)
    if ir_luma <= rgb_luma * 1.3:
        return None  # IR no better (emitter off / sensor covered)
    log.info(
        "IR dark-assist engaged (rgb luma %.0f < %.0f, ir luma %.0f)",
        rgb_luma, dark_luma_threshold(), ir_luma,
    )
    return ir_jpeg, "ir"


def grab_jpeg(allow_ir: bool = True) -> Tuple[bytes, str]:
    """Capture one JPEG frame. Returns (jpeg_bytes, source).

    source is "tracker" (person-tracker frame file), "device" (direct cv2
    open of the RGB cam), or "ir" (infrared dark-room assist — only when
    *allow_ir*; face recognition passes False because SFace embeddings are
    RGB-trained and IR frames must never reach enroll/identify). Raises
    :class:`WebcamError` when no source yields a frame.
    """
    tracker_jpeg = _fresh_tracker_jpeg()
    if tracker_jpeg is not None:
        return tracker_jpeg, "tracker"

    with _capture_lock:
        # Re-check under the lock: a concurrent call may have just confirmed
        # the tracker frame, or the tracker may have started meanwhile.
        tracker_jpeg = _fresh_tracker_jpeg()
        if tracker_jpeg is not None:
            return tracker_jpeg, "tracker"
        rgb_jpeg = _capture_device_jpeg()
        if allow_ir:
            ir = _maybe_ir_assist(rgb_jpeg)
            if ir is not None:
                return ir
        return rgb_jpeg, "device"
