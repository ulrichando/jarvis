"""livekit_track_guard.py — monkey-patch `Room._on_room_event` to
swallow KeyError from stale track SIDs during reconnect.

Catches the exact bug that crashed the voice-client during the
2026-05-04 DNS blip:

    File ".../livekit/rtc/room.py", line 680, in _on_room_event
        lpublication = self.local_participant.track_publications[sid]
    KeyError: 'TR_AMMxN69RnMdE3e'
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from livekit import rtc as _lk_rtc

import livekit_track_guard


def test_install_is_idempotent():
    """Calling install() twice must not double-wrap the method."""
    livekit_track_guard.install()
    first = _lk_rtc.Room._on_room_event
    livekit_track_guard.install()
    second = _lk_rtc.Room._on_room_event
    assert first is second


def test_local_track_unpublished_with_unknown_sid_does_not_crash():
    """Pre-patch this raised KeyError (today's bug). Post-patch the
    guard logs + returns without crashing the listener task."""
    livekit_track_guard.install()

    room = _lk_rtc.Room()
    fake_local = MagicMock()
    fake_local.track_publications = {}
    room.__dict__["_local_participant"] = fake_local

    fake_event = MagicMock()
    fake_event.WhichOneof = lambda _: "local_track_unpublished"
    fake_event.local_track_unpublished.publication_sid = "TR_NOT_REGISTERED"

    # No assertion needed — the call is the assertion. If KeyError
    # leaks, the test fails.
    room._on_room_event(fake_event)


def test_local_track_published_with_unknown_sid_does_not_crash():
    livekit_track_guard.install()
    room = _lk_rtc.Room()
    fake_local = MagicMock()
    fake_local.track_publications = {}
    room.__dict__["_local_participant"] = fake_local

    fake_event = MagicMock()
    fake_event.WhichOneof = lambda _: "local_track_published"
    fake_event.local_track_published.track_sid = "TR_NOT_REGISTERED"

    room._on_room_event(fake_event)


def test_remote_track_unpublished_with_unknown_participant_does_not_crash():
    livekit_track_guard.install()
    room = _lk_rtc.Room()
    room.__dict__["_remote_participants"] = {}

    fake_event = MagicMock()
    fake_event.WhichOneof = lambda _: "track_unpublished"
    fake_event.track_unpublished.participant_identity = "ghost"
    fake_event.track_unpublished.publication_sid = "TR_NOT_REGISTERED"

    room._on_room_event(fake_event)
