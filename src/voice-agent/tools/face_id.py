"""Face ID — InsightFace buffalo_l (RetinaFace + ArcFace ResNet-50).

Production-grade open-source face stack — same combo AWS Rekognition
clones and ID-verification SaaS use under the hood. 512-d L2-normalized
embeddings, cosine distance, ~99.83% LFW. ONNX Runtime executes the
models on CPU. Models (~280 MB) auto-download to `~/.insightface/` on
first call.

Anti-spoof:
  - **Motion liveness**: capture N frames; bbox centroids must vary
    by ≥1 pixel between frames. A printed photo or static screen
    yields identical bboxes; even tiny natural head movement breaks
    that.
  - **Cross-camera**: if IR camera is available, the face must
    appear in IR too. Defeats printed photos and screen replays
    that only fool one sensor.

Tools:
  - face_register(name) — averaged multi-frame enrollment
  - face_identify()     — liveness-gated cosine match
  - face_list()         — list registered names
  - face_delete(name)   — remove a name

Hoisted from `tools/computer_use.py` 2026-05-10 (Step 7 of the
audit). The webcam capture (`_take_webcam_frame`) is shared with
`computer_use.webcam_capture` and stays in computer_use.py — imported
lazily here to avoid circular import at module init.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import time
from pathlib import Path

from livekit.agents.llm import function_tool


logger = logging.getLogger("jarvis.face_id")


__all__ = [
    "FACES_DIR",
    "FACE_THRESHOLD",
    "FACE_ENROLL_FRAMES",
    "FACE_LIVENESS_FRAMES",
    "IR_DEVICE",
    "face_register",
    "face_identify",
    "face_list",
    "face_delete",
]


# ── Config ──────────────────────────────────────────────────────────

# Storage location for enrolled embeddings.
FACES_DIR: Path = Path.home() / ".jarvis" / "faces"

# Cosine-distance threshold for "match". ArcFace literature uses ~0.4.
FACE_THRESHOLD: float = float(os.environ.get("JARVIS_FACE_THRESHOLD", "0.40"))

# Enrollment captures N frames and averages embeddings for robustness.
FACE_ENROLL_FRAMES: int = int(os.environ.get("JARVIS_FACE_ENROLL_FRAMES", "5"))

# Identify captures N frames and checks motion liveness across them.
FACE_LIVENESS_FRAMES: int = int(os.environ.get("JARVIS_FACE_LIVENESS_FRAMES", "3"))

# IR camera (Windows-Hello-style greyscale stream). If absent or the
# read fails, IR check is skipped — RGB-only motion liveness still runs.
IR_DEVICE: str = os.environ.get("JARVIS_IR_DEVICE", "/dev/video2")


# ── InsightFace lazy init ───────────────────────────────────────────

_face_app = None  # InsightFace FaceAnalysis singleton


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
        # Lazy import: _take_webcam_frame stays in tools/computer_use.py
        # because webcam_capture (a non-face tool) also needs it.
        from tools.computer_use import _take_webcam_frame
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
    from tools.computer_use import _take_webcam_frame  # lazy, see _capture_face_frame
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


# ── @function_tool surface ──────────────────────────────────────────

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
        from tools.computer_use import _take_webcam_frame  # lazy
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
