"""Monkey-patch livekit.rtc.Room._on_room_event to swallow KeyError
on track-event dispatch branches during reconnect.

Bug class fixed: when the SFU emits track_unpublished AFTER the local
SDK has already removed the publication during a reconnect (the
windows-of-divergence that Discord, Twilio, and LiveKit's own docs
all flag), the bare dict access raises KeyError in `room.py`'s event
dispatcher and the listener asyncio task dies silently. systemd
keeps the process alive but the agent has no peer.

Patch is idempotent — install() is safe to call multiple times.
Same load-bearing-monkey-patch pattern jarvis_agent.py already uses
for the deepseek roundtrip + tool-name sanitizer + acoustic tap.

Spec: docs/superpowers/specs/2026-05-04-jarvis-voice-resilience-design.md

Scope note: the catch wraps the entire ORIGINAL dispatch including
the subsequent `self.emit(...)` calls that fan out to user-registered
callbacks. If a callback raises KeyError for an unrelated reason
(legitimate bug), this guard would swallow it. We accept this
tradeoff because:
  (a) none of our current callbacks do dict-by-SID lookups —
      see jarvis_voice_client.py's _on_track / _on_speakers / etc;
  (b) the alternative — re-implementing each branch's dict access in
      the guard — is fragile and tracks the SDK's internals;
  (c) DEBUG-level logging means the swallowed exception is
      diagnosable from journalctl when needed.
If a future callback adds a SID-keyed dict lookup, either pre-check
the dict or restructure this guard to be branch-specific.
"""
from __future__ import annotations

import logging

from livekit import rtc

logger = logging.getLogger("jarvis.track_guard")

_INSTALLED = False
_ORIGINAL_ON_ROOM_EVENT = None

# All branches that do bare dict[key] lookups during reconnect.
# track_subscribed / track_unsubscribed do _remote_participants[id]
# THEN track_publications[sid] — same bug class, same reconnect
# window. participant_disconnected uses .pop(identity) without a
# default which is also bare-key access, but it's a per-participant
# event and the SDK swallows it via internal handling — leaving it
# explicitly out of scope; track-only is enough for the documented
# crash class.
_GUARDED_BRANCHES = frozenset({
    "local_track_published",
    "local_track_unpublished",
    "local_track_subscribed",
    "track_published",
    "track_unpublished",
    "track_subscribed",
    "track_unsubscribed",
})


def install() -> None:
    """Replace Room._on_room_event with a guarded version. Idempotent."""
    global _INSTALLED, _ORIGINAL_ON_ROOM_EVENT
    if _INSTALLED:
        return
    _ORIGINAL_ON_ROOM_EVENT = rtc.Room._on_room_event
    rtc.Room._on_room_event = _guarded_on_room_event
    _INSTALLED = True
    logger.info("[track_guard] monkey-patch installed")


def _guarded_on_room_event(self, event):
    """Wrap the original dispatch in a KeyError shield for the
    local_track_* and track_* branches. Anything else passes through
    unchanged so we don't accidentally swallow real bugs."""
    which = event.WhichOneof("message")
    if which not in _GUARDED_BRANCHES:
        return _ORIGINAL_ON_ROOM_EVENT(self, event)

    try:
        return _ORIGINAL_ON_ROOM_EVENT(self, event)
    except KeyError as e:
        logger.debug(
            "[track_guard] swallowed KeyError on %s for sid=%r — "
            "publication already removed during reconnect",
            which, e.args[0] if e.args else e,
        )
        return None
