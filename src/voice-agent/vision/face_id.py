"""Local face recognition — YuNet detection + SFace embeddings (OpenCV Zoo).

Identify-and-remember layer behind the ``face_recognition`` voice tool and the
``webcam`` tool's ``recognized`` enrichment. Fully local: no frame ever leaves
the machine for recognition (the separate ``webcam`` tool's scene description
does call Anthropic; this module does not).

Models — both from the official opencv_zoo repo (Apache-2.0), served via
GitHub's LFS media endpoint, fetched ONCE to ``~/.jarvis/models/``:

  face_detection_yunet_2023mar.onnx      (~232 KB)  cv2.FaceDetectorYN
  face_recognition_sface_2021dec.onnx    (~37 MB)   cv2.FaceRecognizerSF

Run ``python -m vision.face_id fetch`` (in the voice-agent venv) to download.
Downloads are verified two ways: pinned SHA-256 (recorded 2026-06-11 from
opencv_zoo@main — see _MODELS) and a load test (the cv2 constructor must
accept the file; catches LFS pointer files served instead of binaries).

Enrollment store — ``~/.jarvis/faces/faces.json``::

    {"people": {"Alice": [[<128 floats>], ...]}}

Multiple embeddings per person (enroll from a few angles) — matching takes
the best cosine score across samples. SFace's published cosine threshold is
0.363; override via JARVIS_FACE_MATCH_THRESHOLD.

Privacy note: embeddings are stored, not photos. Deleting a person
(``forget``) removes their vectors permanently.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

# SFace cosine-similarity threshold (OpenCV's published value for SFace).
DEFAULT_MATCH_THRESHOLD = 0.363

_LFS_BASE = "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models"

# filename → (url, sha256). Hashes recorded 2026-06-11 from opencv_zoo@main;
# empty string means "not yet pinned" (fetch records + warns, load test still
# applies). Re-pin if upstream legitimately revs the files.
_MODELS: Dict[str, Tuple[str, str]] = {
    "face_detection_yunet_2023mar.onnx": (
        f"{_LFS_BASE}/face_detection_yunet/face_detection_yunet_2023mar.onnx",
        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
    ),
    "face_recognition_sface_2021dec.onnx": (
        f"{_LFS_BASE}/face_recognition_sface/face_recognition_sface_2021dec.onnx",
        "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
    ),
}

_model_lock = threading.Lock()
_store_lock = threading.Lock()
_detector = None
_recognizer = None


class FaceIdError(RuntimeError):
    """Raised for face-ID failures the tool layer should voice to the user."""


# ---------------------------------------------------------------------------
# Paths / config
# ---------------------------------------------------------------------------


def _models_dir() -> Path:
    return Path(
        os.environ.get("JARVIS_FACE_MODELS_DIR", str(Path.home() / ".jarvis" / "models"))
    )


def _detect_model_path() -> Path:
    return _models_dir() / "face_detection_yunet_2023mar.onnx"


def _recog_model_path() -> Path:
    return _models_dir() / "face_recognition_sface_2021dec.onnx"


def _store_path() -> Path:
    return Path(
        os.environ.get(
            "JARVIS_FACES_STORE_FILE", str(Path.home() / ".jarvis" / "faces" / "faces.json")
        )
    )


def _match_threshold() -> float:
    try:
        return float(os.environ.get("JARVIS_FACE_MATCH_THRESHOLD", ""))
    except ValueError:
        return DEFAULT_MATCH_THRESHOLD


def models_present() -> bool:
    """True when both ONNX models exist on disk (enrollment can work)."""
    return _detect_model_path().is_file() and _recog_model_path().is_file()


def recognition_ready() -> bool:
    """True when identification can produce names: models + ≥1 enrolled person."""
    return models_present() and bool(_load_store()["people"])


# ---------------------------------------------------------------------------
# Model download
# ---------------------------------------------------------------------------


def fetch_models(force: bool = False) -> Dict[str, str]:
    """Download both ONNX models to the models dir. Returns name → status.

    Each download is hash-verified against the pinned SHA-256 and load-tested
    through its cv2 constructor before being moved into place.
    """
    import cv2

    _models_dir().mkdir(parents=True, exist_ok=True)
    results: Dict[str, str] = {}
    for filename, (url, pinned_sha) in _MODELS.items():
        dest = _models_dir() / filename
        if dest.is_file() and not force:
            results[filename] = "already present"
            continue

        log.info("fetching %s from %s", filename, url)
        # Keep the .onnx extension on the temp file — cv2.readNet infers the
        # framework from the extension and rejects ".tmp".
        tmp = dest.with_name("_tmp_" + dest.name)
        urllib.request.urlretrieve(url, tmp)  # noqa: S310 — pinned https URL

        sha = hashlib.sha256(tmp.read_bytes()).hexdigest()
        if pinned_sha and sha != pinned_sha:
            tmp.unlink(missing_ok=True)
            raise FaceIdError(
                f"{filename}: downloaded hash {sha} != pinned {pinned_sha} — refusing"
            )
        if not pinned_sha:
            log.warning("%s: no pinned hash; downloaded sha256=%s", filename, sha)

        # Load test — catches LFS pointer files / truncated downloads.
        try:
            if "yunet" in filename:
                cv2.FaceDetectorYN.create(str(tmp), "", (320, 320))
            else:
                cv2.FaceRecognizerSF.create(str(tmp), "")
        except cv2.error as exc:
            tmp.unlink(missing_ok=True)
            raise FaceIdError(f"{filename}: downloaded file failed to load: {exc}") from exc

        tmp.replace(dest)
        results[filename] = f"fetched ({sha[:12]}…)"
    return results


# ---------------------------------------------------------------------------
# Model singletons
# ---------------------------------------------------------------------------


def _get_detector():
    global _detector
    with _model_lock:
        if _detector is None:
            import cv2

            if not _detect_model_path().is_file():
                raise FaceIdError(
                    "face detection model missing — run: python -m vision.face_id fetch"
                )
            _detector = cv2.FaceDetectorYN.create(
                str(_detect_model_path()), "", (320, 320), 0.8, 0.3, 5000
            )
        return _detector


def _get_recognizer():
    global _recognizer
    with _model_lock:
        if _recognizer is None:
            import cv2

            if not _recog_model_path().is_file():
                raise FaceIdError(
                    "face recognition model missing — run: python -m vision.face_id fetch"
                )
            _recognizer = cv2.FaceRecognizerSF.create(str(_recog_model_path()), "")
        return _recognizer


# ---------------------------------------------------------------------------
# Store I/O (atomic, lock-protected)
# ---------------------------------------------------------------------------


def _load_store() -> Dict[str, Any]:
    try:
        data = json.loads(_store_path().read_text())
    except (OSError, ValueError):
        return {"people": {}}
    if not isinstance(data, dict) or not isinstance(data.get("people"), dict):
        return {"people": {}}
    return data


def _save_store(data: Dict[str, Any]) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(path)


def _append_embedding(name: str, embedding: List[float]) -> int:
    """Add one embedding sample for *name*; returns the new sample count."""
    with _store_lock:
        data = _load_store()
        samples = data["people"].setdefault(name, [])
        samples.append(list(map(float, embedding)))
        _save_store(data)
        return len(samples)


def list_people() -> Dict[str, int]:
    """Enrolled people → number of stored embedding samples."""
    return {name: len(samples) for name, samples in _load_store()["people"].items()}


def forget(name: str) -> bool:
    """Remove a person's embeddings entirely. True if they were enrolled."""
    with _store_lock:
        data = _load_store()
        # Case-insensitive match so "forget alice" hits "Alice".
        actual = next(
            (k for k in data["people"] if k.lower() == name.strip().lower()), None
        )
        if actual is None:
            return False
        del data["people"][actual]
        _save_store(data)
        return True


# ---------------------------------------------------------------------------
# Detection / embedding
# ---------------------------------------------------------------------------


def _decode_bgr(jpeg: bytes):
    import cv2
    import numpy as np

    frame = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise FaceIdError("could not decode camera frame")
    return frame


def _detect(frame) -> list:
    """Run YuNet on a BGR frame; returns the (possibly empty) face-row list."""
    detector = _get_detector()
    h, w = frame.shape[:2]
    detector.setInputSize((w, h))
    _, faces = detector.detect(frame)
    return [] if faces is None else list(faces)


def _embed(frame, face_row) -> List[float]:
    """Align+crop one detected face and return its 128-d SFace embedding."""
    recognizer = _get_recognizer()
    aligned = recognizer.alignCrop(frame, face_row)
    feature = recognizer.feature(aligned)
    return [float(x) for x in feature.flatten()]


def _match_score(emb_a: List[float], emb_b: List[float]) -> float:
    """Cosine similarity between two stored embeddings via SFace's matcher."""
    import cv2
    import numpy as np

    recognizer = _get_recognizer()
    a = np.asarray(emb_a, dtype=np.float32).reshape(1, -1)
    b = np.asarray(emb_b, dtype=np.float32).reshape(1, -1)
    return float(recognizer.match(a, b, cv2.FaceRecognizerSF_FR_COSINE))


def _best_name(embedding: List[float]) -> Tuple[Optional[str], float]:
    """Best enrolled match for one embedding: (name, score), or (None, best)."""
    best_name: Optional[str] = None
    best_score = -1.0
    for name, samples in _load_store()["people"].items():
        for sample in samples:
            score = _match_score(embedding, sample)
            if score > best_score:
                best_name, best_score = name, score
    if best_name is not None and best_score >= _match_threshold():
        return best_name, best_score
    return None, best_score


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def enroll(jpeg: bytes, name: str) -> Dict[str, Any]:
    """Learn the (single) face in the frame as *name*.

    Requires exactly one face in frame — enrolling from a crowd would store
    the wrong person. Returns {"name", "samples"}.
    """
    name = name.strip()
    if not name:
        raise FaceIdError("a name is required to enroll a face")
    frame = _decode_bgr(jpeg)
    faces = _detect(frame)
    if not faces:
        raise FaceIdError("no face visible in the camera frame")
    if len(faces) > 1:
        raise FaceIdError(
            f"{len(faces)} faces in frame — need exactly one to enroll {name}"
        )
    samples = _append_embedding(name, _embed(frame, faces[0]))
    return {"name": name, "samples": samples}


def identify_detailed(jpeg: bytes) -> Dict[str, Any]:
    """Identify every face in the frame against the enrollment store.

    Returns {"recognized": [{"name", "score"}...], "unknown_count": int,
    "face_count": int}.
    """
    frame = _decode_bgr(jpeg)
    faces = _detect(frame)
    recognized: List[Dict[str, Any]] = []
    unknown = 0
    seen_names: set[str] = set()
    for row in faces:
        name, score = _best_name(_embed(frame, row))
        if name is None:
            unknown += 1
        elif name not in seen_names:  # one claim per name per frame
            seen_names.add(name)
            recognized.append({"name": name, "score": round(score, 3)})
        else:
            unknown += 1
    recognized.sort(key=lambda r: -r["score"])
    return {
        "recognized": recognized,
        "unknown_count": unknown,
        "face_count": len(faces),
    }


def identify_all(jpeg: bytes) -> List[str]:
    """Just the names of enrolled people visible in the frame."""
    return [r["name"] for r in identify_detailed(jpeg)["recognized"]]


# ---------------------------------------------------------------------------
# CLI: python -m vision.face_id fetch | list
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)
    cmd = sys.argv[1] if len(sys.argv) > 1 else "fetch"
    if cmd == "fetch":
        for fname, status in fetch_models(force="--force" in sys.argv).items():
            print(f"{fname}: {status}")
    elif cmd == "list":
        people = list_people()
        if not people:
            print("no faces enrolled")
        for n, c in people.items():
            print(f"{n}: {c} sample(s)")
    else:
        print(f"unknown command {cmd!r}; use: fetch [--force] | list")
        sys.exit(2)
