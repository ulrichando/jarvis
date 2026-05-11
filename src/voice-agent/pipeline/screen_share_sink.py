"""Subscribe to the voice-client's screen-share track, keep the latest
frame around as JPEG so `screenshot()` (and any other vision tool)
can prefer the in-room stream over a fresh scrot capture.

When the voice-client publishes a SOURCE_SCREENSHARE video track, the
agent gets a `track_subscribed` event. This module's `attach_to_room`
wires a handler that:

  1. Filters for KIND_VIDEO + SOURCE_SCREENSHARE (other video tracks —
     webcam, app-share — are ignored).
  2. Opens a `VideoStream`, async-iterates frames in a background task.
  3. Converts each frame I420→RGBA, JPEG-encodes via Pillow, stores
     `(jpeg_bytes, monotonic_timestamp)` on the session.

Storage shape — `session._jarvis_latest_screen_frame: Optional[tuple[bytes, float]]`:
  - `None` until the first frame lands or after the track is unsubscribed.
  - The float is `time.monotonic()` so consumers can check freshness
    with a simple `now - t < N` instead of pulling in a timezone-aware
    timestamp.

Why we keep the latest frame (not a queue): the consumer is the
vision LLM, which only ever needs "right now". A queue would let
stale frames pile up if the LLM is slow.

Frame-rate budget: at 1280x800 @ 3 fps, RGBA→JPEG runs ~12 ms on this
host. Cheap enough to do on the asyncio loop; if it ever becomes a
problem, move the encode to `loop.run_in_executor`.
"""
from __future__ import annotations

import asyncio
import io
import logging
import time
from typing import Optional

from livekit import rtc


__all__ = ["attach_to_room", "latest_jpeg", "latest_jpeg_global", "JPEG_QUALITY"]


log = logging.getLogger("jarvis.screen_share_sink")


# Match the publisher's resolution unless the user overrode it; PIL
# will resample if the actual frame size differs.
JPEG_QUALITY: int = 75

# Module-level mirror of the session's latest-frame slot.  `tools.
# computer_use._take_screenshot` lives in a module that doesn't have
# the session object on hand; reading from here lets it pick up the
# in-room frame without plumbing session through every call site.
# Kept in sync by `_consume` whenever the session slot is updated.
_GLOBAL_LATEST: Optional[tuple[bytes, float]] = None


def attach_to_room(room: rtc.Room, session) -> None:
    """Register the track-subscribed handler. Idempotent per-room —
    LiveKit dedupes handler registrations on the same callable, but
    we additionally guard via a sentinel on the room so a second call
    on the same room is a no-op."""
    if getattr(room, "_jarvis_screen_share_sink_attached", False):
        return
    room._jarvis_screen_share_sink_attached = True
    session._jarvis_latest_screen_frame = None

    @room.on("track_subscribed")
    def _on_track_subscribed(track, publication, participant) -> None:
        # Many track_subscribed events are audio (mic / TTS). We only
        # care about a remote SOURCE_SCREENSHARE video track.
        try:
            if track.kind != rtc.TrackKind.KIND_VIDEO:
                return
            src = getattr(publication, "source", None)
            if src != rtc.TrackSource.SOURCE_SCREENSHARE:
                return
        except Exception:
            return

        log.info(f"[screen-share-sink] subscribed to {participant.identity} screen track")
        task = asyncio.create_task(
            _consume(track, session),
            name=f"screen-share-sink-{participant.identity}",
        )
        # Stash on the room so an unsubscribe (or a clean disconnect)
        # can cancel us — without a reference, the task keeps running
        # against a dead track and logs a fault on every iteration.
        room._jarvis_screen_share_sink_task = task

    @room.on("track_unsubscribed")
    def _on_track_unsubscribed(track, publication, participant) -> None:
        if track.kind != rtc.TrackKind.KIND_VIDEO:
            return
        if getattr(publication, "source", None) != rtc.TrackSource.SOURCE_SCREENSHARE:
            return
        log.info(f"[screen-share-sink] {participant.identity} unsubscribed screen track")
        t = getattr(room, "_jarvis_screen_share_sink_task", None)
        if t is not None and not t.done():
            t.cancel()
        # Wipe the latest-frame cache so a stale capture can't satisfy
        # a screenshot() call after the publisher goes away.
        session._jarvis_latest_screen_frame = None


async def _consume(track: rtc.Track, session) -> None:
    """Drain frames from `track` and refresh the session's latest
    screen-frame slot."""
    global _GLOBAL_LATEST
    try:
        from PIL import Image
    except Exception as e:
        log.warning(f"[screen-share-sink] Pillow not available, disabling: {e}")
        return

    stream = rtc.VideoStream(track)
    try:
        async for event in stream:
            frame = event.frame
            try:
                # I420 → RGBA so Pillow can ingest it directly. The
                # conversion runs in the FFI (cheap) and returns a new
                # VideoFrame whose data is RGBA bytes.
                rgba = frame.convert(rtc.VideoBufferType.RGBA)
                img = Image.frombytes(
                    "RGBA", (rgba.width, rgba.height), bytes(rgba.data)
                ).convert("RGB")
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=False)
                pair = (buf.getvalue(), time.monotonic())
                session._jarvis_latest_screen_frame = pair
                _GLOBAL_LATEST = pair
            except Exception as e:
                log.debug(f"[screen-share-sink] frame encode failed: {e}")
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.warning(f"[screen-share-sink] consume loop ended: {e}")
    finally:
        # On clean exit, drop the cached frame so `screenshot()` doesn't
        # surface a one-second-old capture forever.
        try:
            session._jarvis_latest_screen_frame = None
        except Exception:
            pass
        _GLOBAL_LATEST = None


def latest_jpeg(session, *, max_age_s: float = 2.0) -> Optional[bytes]:
    """Return the most-recent screen-share JPEG if it's fresher than
    `max_age_s`, else None. Callers fall back to scrot when None."""
    pair = getattr(session, "_jarvis_latest_screen_frame", None)
    if pair is None:
        return None
    jpeg, ts = pair
    if time.monotonic() - ts > max_age_s:
        return None
    return jpeg


def latest_jpeg_global(*, max_age_s: float = 2.0) -> Optional[bytes]:
    """Session-less counterpart for tool modules that don't have a
    session reference. Same freshness semantics."""
    pair = _GLOBAL_LATEST
    if pair is None:
        return None
    jpeg, ts = pair
    if time.monotonic() - ts > max_age_s:
        return None
    return jpeg
