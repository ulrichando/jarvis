"""resilience.track_guard (was livekit_track_guard.py) — monkey-patch `Room._on_room_event` to
swallow KeyError from stale track SIDs during reconnect.

Catches the exact bug that crashed the voice-client during the
2026-05-04 DNS blip:

    File ".../livekit/rtc/room.py", line 680, in _on_room_event
        lpublication = self.local_participant.track_publications[sid]
    KeyError: 'TR_AMMxN69RnMdE3e'
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from livekit import rtc as _lk_rtc

import resilience.track_guard


@pytest.fixture
def fresh_loop():
    """Provide a fresh, set-as-current event loop for the duration of
    one test. Required because `livekit.rtc.Room.__init__` calls
    `asyncio.get_event_loop()` which on Python 3.13 returns None (or
    raises) when no loop is set on the thread. Earlier tests in the
    full suite leave the thread without a loop; in isolation the
    default-policy auto-create masks the problem. Pin the loop here
    so the order of unrelated tests doesn't decide whether Room()
    can construct.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        yield loop
    finally:
        asyncio.set_event_loop(None)
        loop.close()


def test_install_is_idempotent():
    """Calling install() twice must not double-wrap the method."""
    resilience.track_guard.install()
    first = _lk_rtc.Room._on_room_event
    resilience.track_guard.install()
    second = _lk_rtc.Room._on_room_event
    assert first is second


def test_local_track_unpublished_with_unknown_sid_does_not_crash(fresh_loop):
    """Pre-patch this raised KeyError (today's bug). Post-patch the
    guard logs + returns without crashing the listener task."""
    resilience.track_guard.install()

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


def test_local_track_published_with_unknown_sid_does_not_crash(fresh_loop):
    resilience.track_guard.install()
    room = _lk_rtc.Room()
    fake_local = MagicMock()
    fake_local.track_publications = {}
    room.__dict__["_local_participant"] = fake_local

    fake_event = MagicMock()
    fake_event.WhichOneof = lambda _: "local_track_published"
    fake_event.local_track_published.track_sid = "TR_NOT_REGISTERED"

    room._on_room_event(fake_event)


def test_remote_track_unpublished_with_unknown_participant_does_not_crash(fresh_loop):
    resilience.track_guard.install()
    room = _lk_rtc.Room()
    room.__dict__["_remote_participants"] = {}

    fake_event = MagicMock()
    fake_event.WhichOneof = lambda _: "track_unpublished"
    fake_event.track_unpublished.participant_identity = "ghost"
    fake_event.track_unpublished.publication_sid = "TR_NOT_REGISTERED"

    room._on_room_event(fake_event)


def test_unguarded_branch_passes_through_unchanged(fresh_loop):
    """Branches NOT in _GUARDED_BRANCHES must delegate to the
    original `_on_room_event` without the KeyError shield. Verifies
    we don't accidentally widen the catch and swallow real bugs."""
    resilience.track_guard.install()
    import resilience.track_guard as _tg

    calls = []
    saved_original = _tg._ORIGINAL_ON_ROOM_EVENT
    _tg._ORIGINAL_ON_ROOM_EVENT = (
        lambda self, event: calls.append(event) or None
    )
    try:
        fake_event = MagicMock()
        fake_event.WhichOneof = lambda _: "reconnected"

        room = _lk_rtc.Room()
        room._on_room_event(fake_event)
        assert calls == [fake_event], (
            "expected the unguarded branch to delegate exactly once"
        )
    finally:
        _tg._ORIGINAL_ON_ROOM_EVENT = saved_original
