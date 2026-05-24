"""Agent-facing tool schemas + handlers for the google_meet plugin.

Tool surface (stable, identical to the upstream shape):

  meet_join        — join a Google Meet URL, scrape live captions to a transcript
  meet_status      — report bot liveness + transcript progress
  meet_transcript  — read the current transcript (optional last-N)
  meet_leave       — signal the bot to leave cleanly
  meet_say         — speak text into the call (realtime mode)

PORTING NOTE — gated-inert tool surface only.
---------------------------------------------------------------------------
The upstream backend that actually drives a meeting is a large dep-web: a
headless-Chromium process manager (Playwright), a remote-node registry +
WebSocket client, a realtime audio bridge (PulseAudio null-sink / virtual
mic), and an OpenAI Realtime client — well past the 3-4 support-module bar.
That backend is NOT ported here. Instead this module ports the *tool shapes*
plus a clear gate so the surface is stable and ready to wire later.

Gate (``check_meet_requirements``): the tools only reach the LLM surface when
BOTH hold:

  1. ``JARVIS_MEET_ENABLED=1`` — explicit opt-in. Default OFF, so a normal
     voice session never spawns a headless browser into a live call (mirrors
     the project's "no unprompted browser launches" stance).
  2. ``playwright`` is importable AND the platform is Linux/macOS.

Until a meeting backend is wired in, every handler returns a structured
"backend not available in this build" message rather than importing a module
that doesn't exist — so even if the gate is forced on, nothing crashes.
"""

from __future__ import annotations

import json
import os
import platform
from typing import Any, Dict

_TRUE_TOKENS = {"1", "true", "yes", "on"}

# Set by a future meeting-backend integration to flip the handlers from the
# inert "not available" responses to a real implementation. Left None here so
# this build is gated-inert by construction.
_MEET_BACKEND = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Runtime gate
# ---------------------------------------------------------------------------

def _meet_opt_in() -> bool:
    return os.environ.get("JARVIS_MEET_ENABLED", "").strip().lower() in _TRUE_TOKENS


def check_meet_requirements() -> bool:
    """Return True only when the meeting tools should be exposed.

    Requires the explicit ``JARVIS_MEET_ENABLED=1`` opt-in, a supported
    platform (Linux/macOS), and an importable ``playwright`` (the dep the
    real backend would need). Default OFF — the tools stay off the LLM
    surface entirely until deliberately enabled.
    """
    if not _meet_opt_in():
        return False
    if platform.system().lower() not in {"linux", "darwin"}:
        return False
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False
    return True


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

MEET_JOIN_SCHEMA: Dict[str, Any] = {
    "name": "meet_join",
    "description": (
        "Join a Google Meet call and start scraping live captions into a "
        "transcript file. Only meet.google.com URLs are accepted; no calendar "
        "scanning, no auto-dial. Spawns a headless browser subprocess that "
        "runs alongside the agent loop and returns immediately. Poll with "
        "meet_status and read captions with meet_transcript. You should "
        "announce yourself in the meeting — there is no automatic consent "
        "announcement."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Full https://meet.google.com/... URL. Required."},
            "mode": {
                "type": "string",
                "enum": ["transcribe", "realtime"],
                "description": (
                    "transcribe (default): listen-only, scrape captions. "
                    "realtime: also enable agent speech via meet_say."
                ),
            },
            "guest_name": {"type": "string", "description": "Display name when joining as guest. Defaults to 'Assistant'."},
            "duration": {"type": "string", "description": "Optional max duration before auto-leave (e.g. '30m', '2h', '90s')."},
            "headed": {"type": "boolean", "description": "Run the browser headed instead of headless (debug only). Default false."},
        },
        "required": ["url"],
        "additionalProperties": False,
    },
}

MEET_STATUS_SCHEMA: Dict[str, Any] = {
    "name": "meet_status",
    "description": (
        "Report the current Meet session state — whether the bot is alive, has "
        "joined, is in the lobby, number of transcript lines captured, and the "
        "last-caption timestamp."
    ),
    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
}

MEET_TRANSCRIPT_SCHEMA: Dict[str, Any] = {
    "name": "meet_transcript",
    "description": (
        "Read the scraped transcript for the active Meet session. Returns the "
        "full transcript unless 'last' is set, in which case returns only the "
        "last N caption lines."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "last": {
                "type": "integer",
                "description": "Optional: return only the last N caption lines (for polling during a meeting).",
                "minimum": 1,
            },
        },
        "additionalProperties": False,
    },
}

MEET_LEAVE_SCHEMA: Dict[str, Any] = {
    "name": "meet_leave",
    "description": (
        "Leave the active Meet call cleanly, stop caption scraping, and "
        "finalize the transcript file. Safe to call when no meeting is active."
    ),
    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
}

MEET_SAY_SCHEMA: Dict[str, Any] = {
    "name": "meet_say",
    "description": (
        "Speak text into the active Meet call. Requires the meeting to have "
        "been joined with mode='realtime'. Returns immediately; the actual "
        "speech lags by a couple of seconds."
    ),
    "parameters": {
        "type": "object",
        "properties": {"text": {"type": "string", "description": "Text to speak."}},
        "required": ["text"],
        "additionalProperties": False,
    },
}


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _err(msg: str, **extra) -> str:
    return _json({"success": False, "error": msg, **extra})


_BACKEND_UNAVAILABLE = (
    "The Google Meet backend (headless-browser caption bot + realtime audio "
    "bridge) is not wired into this build. The tool surface is present and "
    "gated; connect a meeting backend to enable joining calls."
)


def handle_meet_join(args: Dict[str, Any], **_kw) -> str:
    url = (args.get("url") or "").strip()
    if not url:
        return _err("url is required")
    if "meet.google.com" not in url:
        return _err("only https://meet.google.com/ URLs are accepted")
    mode = (args.get("mode") or "transcribe").strip().lower()
    if mode not in {"transcribe", "realtime"}:
        return _err(f"mode must be 'transcribe' or 'realtime' (got {mode!r})")
    if _MEET_BACKEND is None:
        return _err(_BACKEND_UNAVAILABLE)
    return _json({"success": bool(_MEET_BACKEND.start(  # pragma: no cover - no backend in this build
        url=url,
        headed=bool(args.get("headed", False)),
        guest_name=str(args.get("guest_name") or "Assistant"),
        duration=str(args.get("duration")) if args.get("duration") else None,
        mode=mode,
    ).get("ok"))})


def handle_meet_status(args: Dict[str, Any], **_kw) -> str:
    if _MEET_BACKEND is None:
        return _err(_BACKEND_UNAVAILABLE)
    return _json(_MEET_BACKEND.status())  # pragma: no cover - no backend in this build


def handle_meet_transcript(args: Dict[str, Any], **_kw) -> str:
    last = args.get("last")
    try:
        last_i = int(last) if last is not None else None
        if last_i is not None and last_i < 1:
            last_i = None
    except (TypeError, ValueError):
        last_i = None
    if _MEET_BACKEND is None:
        return _err(_BACKEND_UNAVAILABLE)
    return _json(_MEET_BACKEND.transcript(last=last_i))  # pragma: no cover - no backend in this build


def handle_meet_leave(args: Dict[str, Any], **_kw) -> str:
    if _MEET_BACKEND is None:
        return _err(_BACKEND_UNAVAILABLE)
    return _json(_MEET_BACKEND.stop(reason="agent called meet_leave"))  # pragma: no cover - no backend in this build


def handle_meet_say(args: Dict[str, Any], **_kw) -> str:
    text = (args.get("text") or "").strip()
    if not text:
        return _err("text is required")
    if _MEET_BACKEND is None:
        return _err(_BACKEND_UNAVAILABLE)
    return _json(_MEET_BACKEND.enqueue_say(text))  # pragma: no cover - no backend in this build
