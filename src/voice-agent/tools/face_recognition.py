"""``face_recognition`` tool — enroll, identify, and forget known faces.

Voice surface over :mod:`vision.face_id` (YuNet detection + SFace embeddings,
both local — recognition frames never leave the machine, unlike the `webcam`
tool's Anthropic scene description). One frame is captured per call via
:mod:`vision.webcam` (tracker frame file or direct V4L2 capture) — RGB ONLY:
the IR dark-assist is bypassed (``grab_jpeg(allow_ir=False)``) because SFace
embeddings are RGB-trained. Dark frames get an honest "too dark" answer
instead of "no one is there" — YuNet finding zero faces in a near-black frame
says nothing about the room being empty (live failure 2026-06-11 14:38: user
in frame, luma 23, JARVIS claimed "camera's empty" then confabulated a
covered lens).

Enrollment is deliberate, not ambient: JARVIS learns a face only when told
("this is Alice" / "remember my face"), one person in frame at a time, and
forgets on request. The store lives at ``~/.jarvis/faces/faces.json``
(embeddings only, no photos).

Gating: V4L2 camera hardware present (vision.webcam.webcam_available) AND the
ONNX models fetched (``python -m vision.face_id fetch``). Kill-switch:
``JARVIS_FACE_RECOGNITION_DISABLED=1``. No API key required.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict

from vision import face_id
from vision.webcam import (
    dark_luma_threshold,
    grab_jpeg,
    mean_jpeg_luma,
    webcam_available,
)

from .registry import registry, tool_error, tool_result

logger = logging.getLogger(__name__)

VALID_ACTIONS = ("enroll", "identify", "list", "forget")


def check_face_recognition_requirements() -> bool:
    """Tool gate: hardware present + models fetched + not disabled."""
    if os.environ.get("JARVIS_FACE_RECOGNITION_DISABLED") == "1":
        return False
    return webcam_available() and face_id.models_present()


def _spoken_identify_summary(detail: Dict[str, Any], too_dark: bool = False) -> str:
    names = [r["name"] for r in detail["recognized"]]
    unknown = detail["unknown_count"]
    if not detail["face_count"]:
        if too_dark:
            return (
                "The frame is too dark to detect faces — I can't tell whether "
                "anyone is there. With more light on the face I can check."
            )
        return "I don't see anyone in front of the camera."
    parts = []
    if names:
        parts.append("I recognize " + ", ".join(names) + ".")
    if unknown:
        parts.append(
            f"There {'is one person' if unknown == 1 else f'are {unknown} people'} "
            "I don't recognize."
        )
    return " ".join(parts)


def _handle_face_recognition(args: Dict[str, Any], **_kw: Any) -> str:
    if not isinstance(args, dict):
        return tool_error("invalid arguments")
    action = (args.get("action") or "").strip().lower()
    name = (args.get("name") or "").strip()

    if action not in VALID_ACTIONS:
        return tool_error(
            f"unknown action {action!r}; expected one of {', '.join(VALID_ACTIONS)}"
        )

    if action == "list":
        people = face_id.list_people()
        if not people:
            return tool_result(result="I don't have anyone's face on file yet.", people={})
        summary = ", ".join(f"{n} ({c} sample{'s' if c != 1 else ''})" for n, c in people.items())
        return tool_result(
            result=f"{len(people)} {'person' if len(people) == 1 else 'people'} on file: {summary}.",
            people=people,
        )

    if action == "forget":
        if not name:
            return tool_error("a name is required to forget someone")
        if not face_id.forget(name):
            return tool_error(f"nobody named {name} is on file")
        return tool_result(result=f"Forgotten — {name}'s face data is deleted.")

    # enroll / identify need a live frame — RGB only (allow_ir=False): SFace
    # embeddings are RGB-trained, so the IR night frame must never reach them.
    try:
        jpeg, source = grab_jpeg(allow_ir=False)
    except Exception as exc:  # noqa: BLE001 — capture trouble must not crash the turn
        logger.warning("face_recognition capture failed: %s", exc)
        return tool_error(f"Camera capture failed: {exc}")

    luma = mean_jpeg_luma(jpeg)
    too_dark = luma < dark_luma_threshold()
    if too_dark:
        logger.info("face_recognition %s on dark frame (luma %.0f)", action, luma)

    if action == "enroll":
        if not name:
            return tool_error("a name is required to enroll a face")
        if too_dark:
            # A dark-frame embedding poisons the store: it would never match
            # the same face in daylight, and vice versa.
            return tool_error(
                "The room is too dark to enroll a face reliably — face "
                "recognition needs visible light on the face. Add light "
                "and try again.",
                frame_luma=round(luma),
            )
        try:
            outcome = face_id.enroll(jpeg, name)
        except face_id.FaceIdError as exc:
            return tool_error(str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.warning("face enroll failed: %s", exc)
            return tool_error(f"Enrollment failed: {exc}")
        samples = outcome["samples"]
        tip = " Capture once or twice more from different angles for robustness." if samples < 3 else ""
        return tool_result(
            result=f"Got it — enrolled {outcome['name']} ({samples} sample{'s' if samples != 1 else ''} on file).{tip}",
            name=outcome["name"],
            samples=samples,
        )

    # identify
    try:
        detail = face_id.identify_detailed(jpeg)
    except face_id.FaceIdError as exc:
        return tool_error(str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.warning("face identify failed: %s", exc)
        return tool_error(f"Identification failed: {exc}")
    payload: Dict[str, Any] = {
        "result": _spoken_identify_summary(detail, too_dark=too_dark),
        "recognized": [r["name"] for r in detail["recognized"]],
        "unknown_count": detail["unknown_count"],
        "face_count": detail["face_count"],
        "source": source,
    }
    if too_dark:
        # Context the supervisor must relay instead of inventing physical
        # explanations ("camera's covered") for an undetectable face.
        payload["frame_too_dark"] = True
        payload["frame_luma"] = round(luma)
    return tool_result(payload)


FACE_RECOGNITION_SCHEMA = {
    "name": "face_recognition",
    "description": (
        "Recognize and REMEMBER people by face through the webcam. Fully "
        "local (face embeddings on disk; no cloud call).\n"
        "ACTIONS:\n"
        "  enroll — learn the one face in frame as `name`. Use for 'this is "
        "Alice', 'remember my face' (use the speaker's name), 'learn who I "
        "am'. Exactly one person must be visible.\n"
        "  identify — say who is in front of the camera. Use for 'who am "
        "I?', 'who is this?', 'do you know who's here?', 'is that Bob?'.\n"
        "  list — names currently on file.\n"
        "  forget — delete a person's face data ('forget Bob').\n"
        "Needs visible light on the face (the infrared night-vision sensor "
        "cannot do recognition) — in a dark room it reports the frame is too "
        "dark rather than who is present; relay that and suggest more light.\n"
        "Identity questions ONLY — for scenes, objects, or appearance use "
        "`webcam`; for the screen use `computer_use`."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": list(VALID_ACTIONS),
                "description": "What to do.",
            },
            "name": {
                "type": "string",
                "description": "Person's name — required for enroll and forget.",
            },
        },
        "required": ["action"],
    },
}


registry.register(
    name="face_recognition",
    toolset="webcam",
    schema=FACE_RECOGNITION_SCHEMA,
    handler=_handle_face_recognition,
    check_fn=check_face_recognition_requirements,
    is_async=False,  # cv2 + tiny ONNX inference; adapter offloads via asyncio.to_thread
    emoji="🪪",
)
