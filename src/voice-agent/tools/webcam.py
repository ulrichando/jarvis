"""``webcam`` tool — physical-world vision through the user's camera.

Why a self-contained vision call instead of feeding pixels to the supervisor:
the tool adapter str-coerces every handler result (see tools/_adapter.py), and
the supervisor's FallbackAdapter cascade includes text-only providers (Groq
Llama, DeepSeek) that would 400 on image content in chat_ctx — plus injected
frames would destabilize the prompt prefix cache. So this tool grabs one JPEG
frame (:mod:`vision.webcam`), sends it with the supervisor's question to an
Anthropic vision model out-of-band, and returns the text answer. That gives
the Claude/LiveKit path the same "look at me" capability the gemini/openai
realtime modes get from their native webcam-frame injection
(bin/jarvis-gpt-tools / bin/jarvis-gemini-tools).

Model: ``JARVIS_WEBCAM_VISION_MODEL`` (default ``claude-haiku-4-5`` — the
project's fast-route model; a webcam glance sits mid voice turn, so latency
beats depth. Bump to ``claude-sonnet-4-6`` for richer scene analysis.)

Gating
------
``check_fn`` passes only when a frame source is plausible (device node exists
or the person-tracker frame file is fresh) AND ``ANTHROPIC_API_KEY`` is set.
Headless CI registers the tool inert — the adapter filters it from the
supervisor surface. Kill-switch: ``JARVIS_WEBCAM_DISABLED=1``.

The captured frame is also written (best-effort) to
``~/.jarvis/webcam/last_frame.jpg`` — a single overwritten audit copy so the
user can inspect exactly what JARVIS saw, without unbounded disk growth.

Face-tracker enrichment: when the kiosk person tracker
(:mod:`vision.person_tracker`, opt-in ``JARVIS_PERSON_TRACKER=1``) is live,
its 5 Hz status file (``~/.jarvis/person_tracker.json``) is folded into the
tool result as ``person_detected`` / ``face_count`` — real-time presence data
the vision model can't fake, alongside the frame description.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict

from vision.webcam import grab_jpeg, webcam_available
from vision import ollama_vision

from .registry import registry, tool_error, tool_result
from .runtime import get_jarvis_dir

logger = logging.getLogger(__name__)

DEFAULT_VISION_MODEL = "claude-haiku-4-5"
DEFAULT_QUESTION = "Describe what you see."
MAX_ANSWER_TOKENS = 400

# Status file written by vision/person_tracker.py at ~5 Hz; older than this
# means the tracker is off and its data must not be reported as live.
TRACKER_STATUS_FRESH_S = 2.0

VISION_SYSTEM = (
    "You are the camera eyes of JARVIS, a voice assistant running on the "
    "user's computer. You receive a single frame from the user's webcam plus "
    "a question. Answer the question directly and concisely — one to three "
    "short sentences, suitable for being spoken aloud. When asked to "
    "identify items or objects, name each distinct object you can actually "
    "see, left to right. Describe only what is visible. If the frame is "
    "black, blurry, obstructed, or shows no one, say so plainly. Never "
    "invent people or objects."
)


def _vision_model() -> str:
    return os.environ.get("JARVIS_WEBCAM_VISION_MODEL", DEFAULT_VISION_MODEL)


def _api_timeout_s() -> float:
    try:
        return float(os.environ.get("JARVIS_WEBCAM_VISION_TIMEOUT_S", "20"))
    except ValueError:
        return 20.0


def check_webcam_requirements() -> bool:
    """Tool gate: camera reachable, a vision backend available, not disabled.

    A vision backend = cloud Anthropic (ANTHROPIC_API_KEY) OR the local
    Ollama vision fallback (JARVIS_LOCAL_VISION_ENABLED). Either alone is
    enough, so a fully-offline box with local vision enabled still exposes
    the tool."""
    if os.environ.get("JARVIS_WEBCAM_DISABLED") == "1":
        return False
    if not os.environ.get("ANTHROPIC_API_KEY") and not ollama_vision.ollama_vision_available():
        return False
    return webcam_available()


def _save_last_frame(jpeg: bytes) -> None:
    """Best-effort audit copy; never lets disk trouble fail the turn."""
    try:
        (get_jarvis_dir("webcam") / "last_frame.jpg").write_bytes(jpeg)
    except Exception as exc:  # noqa: BLE001
        logger.debug("webcam audit-frame write failed: %s", exc)


def _tracker_status_path() -> Path:
    return Path(
        os.environ.get(
            "JARVIS_TRACKER_STATUS_FILE",
            str(Path.home() / ".jarvis" / "person_tracker.json"),
        )
    )


def _live_tracker_status() -> Dict[str, Any]:
    """Face-tracker presence data, only when the tracker is demonstrably live.

    Returns ``{person_detected, face_count}`` from the person tracker's status
    file when it is fresh; empty dict when the tracker is off, stale, or the
    file is unreadable. Stale data is worse than none — a 3-hour-old
    "person_detected: true" must never answer "is anyone there?".
    """
    path = _tracker_status_path()
    try:
        if time.time() - path.stat().st_mtime > TRACKER_STATUS_FRESH_S:
            return {}
        payload = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict) or payload.get("error"):
        return {}
    status: Dict[str, Any] = {}
    if "person_detected" in payload:
        status["person_detected"] = bool(payload["person_detected"])
    if "face_count" in payload:
        status["face_count"] = payload["face_count"]
    return status


def _recognized_faces(jpeg: bytes) -> Dict[str, Any]:
    """Names of enrolled people recognized in the frame.

    Delegates to :mod:`vision.face_id` (local YuNet+SFace embeddings).
    Empty dict whenever recognition isn't ready (models not fetched, nobody
    enrolled) or fails — recognition is an enrichment, never a blocker.
    """
    try:
        from vision import face_id

        if not face_id.recognition_ready():
            return {}
        names = face_id.identify_all(jpeg)
    except Exception as exc:  # noqa: BLE001 — enrichment must not fail the turn
        logger.debug("face recognition skipped: %s", exc)
        return {}
    return {"recognized": names} if names else {}


def _analyze_jpeg_anthropic(jpeg: bytes, question: str) -> str:
    """One Anthropic vision call: frame + question → short spoken-style answer."""
    import anthropic

    client = anthropic.Anthropic(timeout=_api_timeout_s(), max_retries=1)
    response = client.messages.create(
        model=_vision_model(),
        max_tokens=MAX_ANSWER_TOKENS,
        system=VISION_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": base64.b64encode(jpeg).decode("ascii"),
                        },
                    },
                    {"type": "text", "text": question},
                ],
            }
        ],
    )
    answer = "".join(
        block.text for block in response.content if getattr(block, "type", "") == "text"
    ).strip()
    if not answer:
        raise RuntimeError("vision model returned no text")
    return answer


def _analyze_jpeg(jpeg: bytes, question: str) -> tuple[str, str]:
    """Frame + question → (answer, model_label).

    Anthropic vision is primary; the local Ollama vision model
    (JARVIS_LOCAL_VISION_ENABLED) is the OFFLINE fallback when Anthropic
    is keyless or errors. Returns the label of the backend that actually
    answered so the caller reports the right model. Raises only when NO
    backend is available / both fail — never fabricates a description
    (the supervisor's confab gate depends on a real tool_result)."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            return _analyze_jpeg_anthropic(jpeg, question), _vision_model()
        except Exception as exc:  # noqa: BLE001 — fall back to local if enabled
            if not ollama_vision.ollama_vision_available():
                raise
            logger.warning(
                "webcam: Anthropic vision failed (%s); falling back to local Ollama vision",
                exc,
            )
            return (
                ollama_vision.analyze_jpeg(jpeg, question, system=VISION_SYSTEM),
                ollama_vision.model_label(),
            )
    # Fully offline: no Anthropic key. Use local vision if enabled.
    if ollama_vision.ollama_vision_available():
        return (
            ollama_vision.analyze_jpeg(jpeg, question, system=VISION_SYSTEM),
            ollama_vision.model_label(),
        )
    raise RuntimeError(
        "no vision backend available (set ANTHROPIC_API_KEY or "
        "JARVIS_LOCAL_VISION_ENABLED=1)"
    )


def _handle_webcam(args: Dict[str, Any], **_kw: Any) -> str:
    question = (args.get("question") or "").strip() if isinstance(args, dict) else ""
    if not question:
        question = DEFAULT_QUESTION

    try:
        jpeg, source = grab_jpeg()
    except Exception as exc:  # noqa: BLE001 — capture trouble must not crash the turn
        logger.warning("webcam capture failed: %s", exc)
        return tool_error(
            f"Webcam capture failed: {exc}",
            hint="Camera may be unplugged, disabled, or held by another app.",
        )

    _save_last_frame(jpeg)

    # IR dark-assist frames are greyscale night vision — tell the vision
    # model so it doesn't guess at colors or call the scene "black and white".
    question_for_model = question
    if source == "ir":
        question_for_model += (
            " (Note: this frame is from the infrared night-vision camera — "
            "greyscale; colors are not visible. The room is dark.)"
        )

    try:
        answer, model_used = _analyze_jpeg(jpeg, question_for_model)
    except Exception as exc:  # noqa: BLE001 — provider errors must not crash the turn
        logger.warning("webcam vision analysis failed: %s", exc)
        return tool_error(f"Webcam vision analysis failed: {exc}")

    payload: Dict[str, Any] = {
        "result": answer,
        "source": source,
        "model": model_used,
    }
    payload.update(_live_tracker_status())
    if source != "ir":
        # SFace embeddings are RGB-trained — IR night frames must never feed
        # recognition (greyscale + emitter lighting → garbage matches).
        payload.update(_recognized_faces(jpeg))
    return tool_result(payload)


WEBCAM_SCHEMA = {
    "name": "webcam",
    "description": (
        "Look through the user's physical webcam and answer a question about "
        "what the camera sees: the user themself, their appearance, what "
        "they're holding or wearing, who or what is in the room, lighting.\n"
        "USE WHEN the user says things like: 'look at me', 'can you see me?', "
        "'what am I holding?', 'how do I look?', 'is anyone behind me?', "
        "'what color is this?' (holding an object up).\n"
        "This is the CAMERA, not the screen — for windows, apps, or anything "
        "ON the monitor use computer_use instead.\n"
        "Works in low light too: when the room is dark it falls back to the "
        "infrared night-vision sensor automatically (greyscale).\n"
        "Returns a short text description from a vision model; relay it to "
        "the user naturally in your own voice."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": (
                    "What to determine from the camera frame, phrased as the "
                    "user's actual question (e.g. 'What is the user holding "
                    "up to the camera?')."
                ),
            },
        },
        "required": ["question"],
    },
}


registry.register(
    name="webcam",
    toolset="webcam",
    schema=WEBCAM_SCHEMA,
    handler=_handle_webcam,
    check_fn=check_webcam_requirements,
    requires_env=["ANTHROPIC_API_KEY"],
    is_async=False,  # sync SDK + cv2; the adapter offloads sync handlers via asyncio.to_thread
    emoji="📷",
)
