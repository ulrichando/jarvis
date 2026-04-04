"""JARVIS Camera Vision — see through the webcam.

Captures frames from the webcam and can describe what it sees
using the AI reasoner.
"""

import cv2
import base64
from pathlib import Path

CAPTURE_DIR = Path("/tmp/jarvis_camera")
CAPTURE_DIR.mkdir(exist_ok=True)


def capture_frame(camera_id: int = 0) -> str | None:
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

        path = str(CAPTURE_DIR / "webcam.jpg")
        cv2.imwrite(path, frame)
        return path
    except Exception:
        return None


def capture_to_base64(camera_id: int = 0) -> str | None:
    """Capture a frame and return as base64 JPEG."""
    path = capture_frame(camera_id)
    if not path:
        return None
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def is_camera_available(camera_id: int = 0) -> bool:
    """Check if a camera is accessible."""
    try:
        cap = cv2.VideoCapture(camera_id)
        available = cap.isOpened()
        cap.release()
        return available
    except Exception:
        return False


def list_cameras(max_check: int = 5) -> list[int]:
    """Find available camera IDs."""
    available = []
    for i in range(max_check):
        try:
            cap = cv2.VideoCapture(i)
            if cap.isOpened():
                available.append(i)
            cap.release()
        except Exception:
            pass
    return available
