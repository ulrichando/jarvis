"""google_meet plugin — join a Meet call, transcribe live captions, follow up.

Lets the agent join an explicitly-provided ``https://meet.google.com/`` URL,
scrape live captions into a transcript, and (in realtime mode) speak into the
call. Explicit-by-design: only joins URLs passed in — no calendar scanning, no
auto-dial, no automatic consent announcement.

PORTING SCOPE — gated-inert tool surface.
---------------------------------------------------------------------------
This ports the 5-tool surface (``meet_join``, ``meet_status``,
``meet_transcript``, ``meet_leave``, ``meet_say``) and the registration shape.
The heavy backend that actually drives a meeting — a headless-Chromium process
manager (Playwright), a remote-node registry + WebSocket client, a realtime
audio bridge (PulseAudio null-sink / virtual mic), and an OpenAI Realtime
client — is a >3-4-module dep-web and is NOT ported. See ``tools.py`` for the
gate and the "backend not available" handler responses.

The tools gate on ``check_meet_requirements`` (default OFF — requires
``JARVIS_MEET_ENABLED=1`` + importable Playwright on Linux/macOS), so on a
normal voice session they stay completely off the LLM surface.
"""

from __future__ import annotations

import logging
import platform

from plugins.google_meet.tools import (
    MEET_JOIN_SCHEMA,
    MEET_LEAVE_SCHEMA,
    MEET_SAY_SCHEMA,
    MEET_STATUS_SCHEMA,
    MEET_TRANSCRIPT_SCHEMA,
    check_meet_requirements,
    handle_meet_join,
    handle_meet_leave,
    handle_meet_say,
    handle_meet_status,
    handle_meet_transcript,
)

logger = logging.getLogger(__name__)


_TOOLS = (
    ("meet_join",       MEET_JOIN_SCHEMA,       handle_meet_join,       "📞"),
    ("meet_status",     MEET_STATUS_SCHEMA,     handle_meet_status,     "🟢"),
    ("meet_transcript", MEET_TRANSCRIPT_SCHEMA, handle_meet_transcript, "📝"),
    ("meet_leave",      MEET_LEAVE_SCHEMA,      handle_meet_leave,      "👋"),
    ("meet_say",        MEET_SAY_SCHEMA,        handle_meet_say,        "🗣️"),
)


def register(ctx) -> None:
    """Register the Meet tools. Called once by the plugin loader."""
    # Windows is not supported — audio routing for realtime mode has no tested
    # path there and guest-join is flakier. Refuse rather than half-register.
    system = platform.system().lower()
    if system not in {"linux", "darwin"}:
        logger.info("google_meet plugin: platform=%s not supported (linux/macos only)", system)
        return

    for name, schema, handler, emoji in _TOOLS:
        ctx.register_tool(
            name=name,
            toolset="google_meet",
            schema=schema,
            handler=handler,
            check_fn=check_meet_requirements,
            emoji=emoji,
        )
