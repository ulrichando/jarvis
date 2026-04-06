"""JARVIS Camera Vision — see through the webcam.

Supports dual cameras:
  /dev/video0 — RGB webcam (1280x720, 30fps) — general vision, scene description
  /dev/video2 — IR camera (576x360, 15fps) — face ID, works in any lighting

Captures frames from either camera and can describe what it sees
using the AI reasoner.
"""

import os
import cv2
import base64
from pathlib import Path

# Suppress OpenCV stderr spam on camera probe failures
os.environ.setdefault("OPENCV_LOG_LEVEL", "SILENT")
cv2.setLogLevel(0)

CAPTURE_DIR = Path("/tmp/jarvis_camera")
CAPTURE_DIR.mkdir(exist_ok=True)

# Camera device mapping — auto-detected from hardware
def _detect_camera_ids():
    try:
        from src.hardware import detect_hardware
        hw = detect_hardware(include_cameras=True)
        return hw.rgb_camera_id, hw.ir_camera_id
    except Exception:
        return 0, 2  # sensible defaults

RGB_CAMERA, IR_CAMERA = _detect_camera_ids()


def capture_frame(camera_id: int = RGB_CAMERA) -> str | None:
    """Capture a single frame from the webcam. Returns file path or None."""
    try:
        cap = cv2.VideoCapture(camera_id)
        if not cap.isOpened():
            return None

        # Read a few frames to let camera adjust
        for _ in range(5):
            cap.read()

        ret, frame = cap.read()
        cap.release()

        if not ret:
            return None

        suffix = "ir" if camera_id == IR_CAMERA else "rgb"
        path = str(CAPTURE_DIR / f"webcam_{suffix}.jpg")
        cv2.imwrite(path, frame)
        return path
    except Exception:
        return None


def capture_to_base64(camera_id: int = RGB_CAMERA) -> str | None:
    """Capture a frame and return as base64 JPEG."""
    path = capture_frame(camera_id)
    if not path:
        return None
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def capture_ir_frame() -> str | None:
    """Capture from the IR camera — for face ID operations."""
    return capture_frame(IR_CAMERA)


def capture_ir_to_base64() -> str | None:
    """Capture IR frame as base64 — for face recognition."""
    return capture_to_base64(IR_CAMERA)


def capture_both() -> dict:
    """Capture from both RGB and IR cameras simultaneously.
    Returns dict with 'rgb' and 'ir' paths (either may be None).
    """
    return {
        "rgb": capture_frame(RGB_CAMERA),
        "ir": capture_frame(IR_CAMERA),
    }


def is_camera_available(camera_id: int = RGB_CAMERA) -> bool:
    """Check if a camera is accessible."""
    try:
        cap = cv2.VideoCapture(camera_id)
        available = cap.isOpened()
        cap.release()
        return available
    except Exception:
        return False


def has_ir_camera() -> bool:
    """Check if the IR face ID camera is available."""
    return is_camera_available(IR_CAMERA)


def list_cameras(max_check: int = 5) -> list[dict]:
    """Find available camera IDs with type info."""
    available = []
    for i in range(max_check):
        try:
            cap = cv2.VideoCapture(i)
            if cap.isOpened():
                w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                cap.release()
                cam_type = "ir" if i == IR_CAMERA else "rgb"
                available.append({"id": i, "type": cam_type, "resolution": f"{w}x{h}"})
            else:
                cap.release()
        except Exception:
            pass
    return available
