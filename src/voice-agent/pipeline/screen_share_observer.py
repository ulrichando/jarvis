"""Continuous screen-share observer.

While a SOURCE_SCREENSHARE track is subscribed, this module runs a
background poll task that periodically calls `vision_describe()` on
the latest cached frame and parks the resulting text on
`session._jarvis_latest_screen_description`. The `screenshot()` tool
then prefers the cached description over paying the ~4s vision-LLM
tax per query.

Design — why polling, not Live API:

  The Gemini Live API smoke-test 2026-05-11 evening: 3.1-flash-live-
  preview returned 1011 INTERNAL (same as commit 0a0abae) and the
  2.5-flash-native-audio variants require AUDIO modality + return ~4s
  first-token anyway. Live's real advantage is continuous attention
  inside a single warm session — but for our usage pattern (user
  asks intermittently while screen-share is on), every "what's on
  my screen?" pays the session-startup tax just like one-shot
  generate_content. No win.

  Polling with regular generate_content + caching the latest
  description gets the same UX (~0s response when the user asks)
  without WebSocket complexity. Cost is bounded: at the default
  5s poll interval, that's 12 calls/min of gemini-2.5-flash-lite —
  roughly $0.07 per hour of screen-share at current pricing.

Lifecycle:

  - Observer auto-starts when a SOURCE_SCREENSHARE track is
    subscribed (hooked into the same `track_subscribed` event as
    the existing sink).
  - Auto-stops on `track_unsubscribed` or room disconnect.
  - Single observer per session — re-subscription replaces the
    previous task cleanly.

Storage shape — `session._jarvis_latest_screen_description: Optional[tuple[str, float]]`:
  - `None` until the first poll completes, or after the track
    is unsubscribed.
  - The float is `time.monotonic()` so consumers check freshness
    with `now - t < N`.

Module-level `_GLOBAL_LATEST` mirrors the session slot so the
session-less `tools.computer_use.screenshot()` fast path can read
it without plumbing the session through every call site.

Config (read from pipeline.config so env-var changes propagate):
  - `JARVIS_SCREEN_OBSERVER_ENABLED` (default true) — toggle.
  - `JARVIS_SCREEN_OBSERVER_INTERVAL_S` (default 5.0) — poll period.
    Lower = fresher descriptions, higher cost.
  - `JARVIS_SCREEN_OBSERVER_MAX_AGE_S` (default 10.0) — how stale
    a cached description can be before `latest_description()`
    returns None and callers fall through to an on-demand describe.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

from livekit import rtc


__all__ = [
    "attach_to_room",
    "latest_description",
    "latest_description_global",
    "OBSERVER_INTERVAL_S",
    "OBSERVER_MAX_AGE_S",
    "is_enabled",
]


log = logging.getLogger("jarvis.screen_share_observer")


# Read from env at call time (not import time) so config changes via
# the tray UI or systemd reload propagate without a worker restart.
def _enabled() -> bool:
    return os.environ.get("JARVIS_SCREEN_OBSERVER_ENABLED", "1") not in ("0", "false", "")


def is_enabled() -> bool:
    return _enabled()


# Module-level so test code can monkey-patch them without going through env.
OBSERVER_INTERVAL_S: float = float(os.environ.get("JARVIS_SCREEN_OBSERVER_INTERVAL_S", "5.0"))
OBSERVER_MAX_AGE_S: float  = float(os.environ.get("JARVIS_SCREEN_OBSERVER_MAX_AGE_S", "10.0"))


# Mirror of the session's latest-description slot; lets the session-
# less `tools.computer_use.screenshot()` read it without a session ref.
_GLOBAL_LATEST: Optional[tuple[str, float]] = None


# Prompt for the periodic describe call. The supervisor reads this
# from cache when the user asks about the screen, then composes the
# voice reply. So the cache needs to be CONTENT-RICH — filenames,
# error messages, headings — not just "a code editor is open".
# 2-4 sentences. The supervisor compresses further for voice.
# Live failure 2026-05-11 12:48: observer cached only generic
# descriptions ("This is a screenshot of Visual Studio Code, where
# the user is looking at a file"), so when the user asked "can you
# read this .gitignore file?" JARVIS replied "I can't make out the
# specific file names" — Gemini Flash Lite CAN read the text, we
# just told it not to.
_OBSERVER_PROMPT: str = (
    "Describe what's on this screen in 2-4 sentences for a voice "
    "assistant to relay later. INCLUDE the app name AND any readable "
    "text that matters: filenames open in editors, the specific "
    "error message or stack trace, page titles, headings, or the URL "
    "bar. If a code editor is open, name the file. If a terminal is "
    "showing output, quote the relevant line. If a web page is open, "
    "name the site and the headline. Skip pure decoration. The user "
    "WILL ask about specific text content — capture it now."
)


def attach_to_room(room: rtc.Room, session) -> None:
    """Register the screen-observer track handlers. Idempotent per-room.

    Mirrors the lifecycle pattern in screen_share_sink — same
    track_subscribed / track_unsubscribed hooks, same SOURCE_SCREENSHARE
    filter, same sentinel-on-room guard. Does NOT depend on the sink
    being attached, but typically it IS (the sink populates the JPEG
    cache that this observer reads from)."""
    if getattr(room, "_jarvis_screen_observer_attached", False):
        return
    room._jarvis_screen_observer_attached = True
    session._jarvis_latest_screen_description = None

    if not _enabled():
        log.info("[screen-observer] disabled via JARVIS_SCREEN_OBSERVER_ENABLED=0")
        return

    @room.on("track_subscribed")
    def _on_track_subscribed(track, publication, participant) -> None:
        try:
            if track.kind != rtc.TrackKind.KIND_VIDEO:
                return
            src = getattr(publication, "source", None)
            if src != rtc.TrackSource.SOURCE_SCREENSHARE:
                return
        except Exception:
            return

        log.info(
            f"[screen-observer] starting periodic describe loop "
            f"(interval={OBSERVER_INTERVAL_S}s) for {participant.identity}"
        )
        # Cancel any prior task from a previous subscription on this room.
        prev = getattr(room, "_jarvis_screen_observer_task", None)
        if prev is not None and not prev.done():
            prev.cancel()

        task = asyncio.create_task(
            _poll_loop(session),
            name=f"screen-observer-{participant.identity}",
        )
        room._jarvis_screen_observer_task = task

    @room.on("track_unsubscribed")
    def _on_track_unsubscribed(track, publication, participant) -> None:
        if track.kind != rtc.TrackKind.KIND_VIDEO:
            return
        if getattr(publication, "source", None) != rtc.TrackSource.SOURCE_SCREENSHARE:
            return
        log.info(f"[screen-observer] {participant.identity} stopped sharing — cancelling loop")
        t = getattr(room, "_jarvis_screen_observer_task", None)
        if t is not None and not t.done():
            t.cancel()
        # Wipe the cached description so a stale read can't satisfy a
        # screenshot() call after the publisher goes away.
        session._jarvis_latest_screen_description = None
        global _GLOBAL_LATEST
        _GLOBAL_LATEST = None


async def _poll_loop(session) -> None:
    """Poll the sink's latest-JPEG slot on a fixed interval, run
    vision_describe(), publish the text to session + module slots.

    Robust to: stale frames (skip if no JPEG yet), individual call
    failures (logged, loop continues), task cancellation (clean exit).
    """
    global _GLOBAL_LATEST
    try:
        # Lazy imports — keep module import cheap and dodge any
        # circular-import risk with tools.computer_use → _vision_backend.
        from pipeline.screen_share_sink import latest_jpeg_global
        from tools._vision_backend import vision_describe
    except Exception as e:
        log.warning(f"[screen-observer] cannot start — import failed: {e}")
        return

    iteration = 0
    while True:
        iteration += 1
        try:
            await asyncio.sleep(OBSERVER_INTERVAL_S)
            # Use a tight freshness threshold here — if the sink's
            # frame is more than 2× our poll interval old, the share
            # is probably paused or the publisher is stalled; skip
            # rather than describing a stale frame.
            jpeg = latest_jpeg_global(max_age_s=OBSERVER_INTERVAL_S * 2.0)
            if jpeg is None:
                continue

            t0 = time.monotonic()
            try:
                desc = await vision_describe(
                    jpeg, mime_type="image/jpeg", prompt=_OBSERVER_PROMPT
                )
            except Exception as e:
                log.debug(f"[screen-observer] describe failed (iter={iteration}): {e}")
                continue
            elapsed = time.monotonic() - t0

            text = (desc or "").strip()
            if not text:
                continue
            pair = (text, time.monotonic())
            session._jarvis_latest_screen_description = pair
            _GLOBAL_LATEST = pair
            # INFO-level — so the live log shows the loop is running.
            # 12 calls/min at the default interval; not noisy enough to
            # drown other events out, and the visibility is worth it
            # for "is the observer alive?" debugging.
            log.info(
                f"[screen-observer] iter={iteration} described in {elapsed:.2f}s: "
                f"{text[:80]}"
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning(f"[screen-observer] loop iteration {iteration} raised: {e}")
            # Don't tight-loop on errors.
            await asyncio.sleep(OBSERVER_INTERVAL_S)


def latest_description(session, *, max_age_s: float = OBSERVER_MAX_AGE_S) -> Optional[str]:
    """Return the latest cached screen description if fresher than
    `max_age_s`, else None. None tells callers to fall through to an
    on-demand describe (or scrot + vision_describe)."""
    pair = getattr(session, "_jarvis_latest_screen_description", None)
    if pair is None:
        return None
    text, ts = pair
    if time.monotonic() - ts > max_age_s:
        return None
    return text


def latest_description_global(*, max_age_s: float = OBSERVER_MAX_AGE_S) -> Optional[str]:
    """Session-less counterpart for tools that don't have a session
    reference. Same freshness semantics."""
    pair = _GLOBAL_LATEST
    if pair is None:
        return None
    text, ts = pair
    if time.monotonic() - ts > max_age_s:
        return None
    return text
