"""Lightweight person/face tracker for the kiosk avatar.

Captures from the default webcam, runs OpenCV face detection on every frame,
and publishes the primary face's position + size to a JSON status file that
the voice-client's /status endpoint can surface. The face rendering
(FaceWebGL.jsx) reads this to adjust the avatar's gaze direction.

Zero extra dependencies beyond OpenCV (already installed). Designed to run as
a background daemon thread inside the voice-client process, or standalone via:

    python -m vision.person_tracker

Env vars:
  JARVIS_WEBCAM_DEVICE  — device path (default: /dev/video0)
  JARVIS_WEBCAM_RES     — WxH (default: 640x480)
  JARVIS_TRACKER_FPS    — capture rate (default: 15)
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
WEBCAM_DEVICE = os.environ.get("JARVIS_WEBCAM_DEVICE", "/dev/video0")
WEBCAM_INDEX  = int(os.environ.get("JARVIS_WEBCAM_INDEX", "0"))
WEBCAM_RES_W  = int(os.environ.get("JARVIS_WEBCAM_RES", "640x480").split("x")[0])
WEBCAM_RES_H  = int(os.environ.get("JARVIS_WEBCAM_RES", "640x480").split("x")[1])
TRACKER_FPS   = float(os.environ.get("JARVIS_TRACKER_FPS", "15"))
STATUS_FILE   = Path(os.environ.get("JARVIS_TRACKER_STATUS_FILE",
                                    str(Path.home() / ".jarvis" / "person_tracker.json")))
FRAME_FILE    = Path(os.environ.get("JARVIS_TRACKER_FRAME_FILE",
                                    str(Path.home() / ".jarvis" / "person_tracker.jpg")))

# ---------------------------------------------------------------------------
# Face detector (lazy-loaded singleton)
# ---------------------------------------------------------------------------
_face_cascade: Optional["cv2.CascadeClassifier"] = None

def _get_detector():
    global _face_cascade
    if _face_cascade is None:
        import cv2
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        _face_cascade = cv2.CascadeClassifier(cascade_path)
        if _face_cascade.empty():
            raise RuntimeError(f"Failed to load Haar cascade from {cascade_path}")
    return _face_cascade


# ---------------------------------------------------------------------------
# Detection result
# ---------------------------------------------------------------------------
def detect_faces(frame: np.ndarray) -> list[dict]:
    """Return list of detected faces as {x, y, w, h, center_x, center_y}."""
    import cv2
    detector = _get_detector()
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)
    faces = detector.detectMultiScale(
        gray, scaleFactor=1.1, minNeighbors=5,
        minSize=(40, 40), flags=cv2.CASCADE_SCALE_IMAGE,
    )
    h, w = frame.shape[:2]
    results = []
    for (fx, fy, fw, fh) in faces:
        results.append({
            "x": int(fx),
            "y": int(fy),
            "w": int(fw),
            "h": int(fh),
            "center_x": round(float(fx + fw / 2) / w, 4),  # 0..1 normalized
            "center_y": round(float(fy + fh / 2) / h, 4),  # 0..1 normalized
            "size_ratio": round(float(fw * fh) / (w * h), 4),  # 0..1 face area ratio
        })
    return results


# ---------------------------------------------------------------------------
# Tracker state (shared, thread-safe via GIL for simple dict writes)
# ---------------------------------------------------------------------------
_tracker_state: dict = {
    "faces": [],
    "primary_face": None,
    "person_detected": False,
    "fps": 0.0,
    "running": False,
    "last_frame_ts": 0.0,
    "error": None,
}
_state_lock = threading.Lock()


def get_state() -> dict:
    """Return a copy of the current tracker state (thread-safe)."""
    with _state_lock:
        return dict(_tracker_state)


def _write_status_file():
    """Atomically write tracker state to STATUS_FILE for external consumers."""
    try:
        STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATUS_FILE.with_suffix(".tmp")
        with _state_lock:
            payload = {
                "person_detected": _tracker_state["person_detected"],
                "primary_face": _tracker_state["primary_face"],
                "fps": round(_tracker_state["fps"], 1),
                "error": _tracker_state["error"],
            }
        tmp.write_text(json.dumps(payload))
        tmp.replace(STATUS_FILE)
    except Exception:
        pass  # best-effort; don't crash the tracker on I/O errors


# ---------------------------------------------------------------------------
# Main loop (runs in background thread)
# ---------------------------------------------------------------------------
def _tracker_loop() -> None:
    import cv2

    cap = cv2.VideoCapture(WEBCAM_INDEX)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, WEBCAM_RES_W)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, WEBCAM_RES_H)
    cap.set(cv2.CAP_PROP_FPS, TRACKER_FPS)

    if not cap.isOpened():
        with _state_lock:
            _tracker_state["error"] = f"Cannot open webcam {WEBCAM_INDEX} ({WEBCAM_DEVICE})"
            _tracker_state["running"] = False
        log.error(_tracker_state["error"])
        return

    log.info("person-tracker started device=%s res=%dx%d fps=%.0f",
             WEBCAM_DEVICE, WEBCAM_RES_W, WEBCAM_RES_H, TRACKER_FPS)

    with _state_lock:
        _tracker_state["running"] = True
        _tracker_state["error"] = None

    frame_interval = 1.0 / TRACKER_FPS
    last_write = 0.0
    frame_count = 0
    fps_update_ts = time.monotonic()

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                time.sleep(0.1)
                continue

            frame_count += 1
            now = time.monotonic()

            # Run detection
            try:
                faces = detect_faces(frame)
            except Exception as e:
                faces = []
                log.debug("face detection error: %s", e)

            # Update state
            primary = faces[0] if faces else None
            with _state_lock:
                _tracker_state["faces"] = faces
                _tracker_state["primary_face"] = primary
                _tracker_state["person_detected"] = len(faces) > 0
                _tracker_state["last_frame_ts"] = now

            # FPS calculation (once per second)
            if now - fps_update_ts >= 1.0:
                with _state_lock:
                    _tracker_state["fps"] = frame_count / (now - fps_update_ts)
                frame_count = 0
                fps_update_ts = now

            # Write status file + frame JPEG at ~5 Hz
            if now - last_write >= 0.2:
                _write_status_file()
                try:
                    import cv2 as _cv2
                    _cv2.imwrite(str(FRAME_FILE), frame,
                                 [_cv2.IMWRITE_JPEG_QUALITY, 75])
                except Exception:
                    pass
                last_write = now

            # Sleep to maintain target FPS
            elapsed = time.monotonic() - now
            sleep_time = max(0.001, frame_interval - elapsed)
            time.sleep(sleep_time)

    except Exception as e:
        log.exception("person-tracker loop crashed: %s", e)
        with _state_lock:
            _tracker_state["error"] = str(e)
            _tracker_state["running"] = False
    finally:
        cap.release()
        with _state_lock:
            _tracker_state["running"] = False
        log.info("person-tracker stopped")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
_tracker_thread: Optional[threading.Thread] = None

def start(daemon: bool = True) -> threading.Thread:
    """Start the person tracker in a background daemon thread. Idempotent."""
    global _tracker_thread
    if _tracker_thread is not None and _tracker_thread.is_alive():
        log.debug("person-tracker already running")
        return _tracker_thread
    _tracker_thread = threading.Thread(
        target=_tracker_loop, name="person-tracker", daemon=daemon,
    )
    _tracker_thread.start()
    return _tracker_thread


def stop() -> None:
    """Signal the tracker to stop (it will exit on next frame read)."""
    global _tracker_thread
    _tracker_thread = None  # daemon thread will die with process


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Starting person tracker...")
    t = start(daemon=False)
    try:
        while t.is_alive():
            state = get_state()
            pf = state.get("primary_face")
            if pf:
                print(f"\r  face at ({pf['center_x']:.2f}, {pf['center_y']:.2f}) "
                      f"size={pf['size_ratio']:.3f}  fps={state['fps']:.1f}  ", end="")
            else:
                print(f"\r  no face detected  fps={state['fps']:.1f}  ", end="")
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("\nStopped.")
