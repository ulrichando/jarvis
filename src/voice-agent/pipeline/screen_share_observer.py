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


# Observer mode (2026-05-28, Gemini Live streaming added).
#   "polling" (default) — one-shot generate_content per
#     OBSERVER_INTERVAL_S against gemini-2.5-flash-lite. Single still
#     frame per describe. Cheap, reliable, no motion understanding.
#   "stream" — persistent Gemini Live WebSocket session.
#     Frames stream at 1 FPS via send_realtime_input(media=Blob);
#     a text prompt fires every STREAM_PROMPT_INTERVAL_S to elicit a
#     description against the live frame context; responses arrive as
#     TEXT (not AUDIO). session_resumption + context_window_compression
#     keep the socket alive past the documented 2-min audio+video cap.
#     Motion-aware: Gemini sees the SEQUENCE of frames as they arrive.
OBSERVER_MODE: str = os.environ.get("JARVIS_SCREEN_OBSERVER_MODE", "polling").lower()

# Streaming frame rate (≤ 1 FPS per Google docs).
STREAM_FRAME_INTERVAL_S: float = float(
    os.environ.get("JARVIS_SCREEN_OBSERVER_STREAM_FRAME_INTERVAL_S", "1.0")
)
# How often to send a "describe what's on screen now" text prompt to
# elicit a refreshed description. 5s = description never older than ~5s.
STREAM_PROMPT_INTERVAL_S: float = float(
    os.environ.get("JARVIS_SCREEN_OBSERVER_STREAM_PROMPT_INTERVAL_S", "5.0")
)
# Live model. `gemini-3.1-flash-live-preview` is what this project's
# key has access to (verified via models.list).
LIVE_MODEL: str = os.environ.get(
    "JARVIS_SCREEN_OBSERVER_LIVE_MODEL", "gemini-3.1-flash-live-preview"
)


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

        if OBSERVER_MODE == "stream":
            log.info(
                f"[screen-observer] starting Gemini Live STREAM "
                f"(model={LIVE_MODEL}, frames@{STREAM_FRAME_INTERVAL_S}s, "
                f"prompts@{STREAM_PROMPT_INTERVAL_S}s) for {participant.identity}"
            )
            loop_coro = _stream_loop(session)
        else:
            log.info(
                f"[screen-observer] starting periodic describe loop "
                f"(interval={OBSERVER_INTERVAL_S}s) for {participant.identity}"
            )
            loop_coro = _poll_loop(session)
        # Cancel any prior task from a previous subscription on this room.
        prev = getattr(room, "_jarvis_screen_observer_task", None)
        if prev is not None and not prev.done():
            prev.cancel()

        task = asyncio.create_task(
            loop_coro,
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


# ── Streaming mode (Gemini Live, proper continuous video, 2026-05-28) ──
#
# Based on official Google docs research (researcher agent output
# 2026-05-28):
#   - send_realtime_input(media=Blob(jpeg)) for streaming frames (NOT
#     send_client_content — that's for turn-based discrete messages).
#   - Hard cap ≤1 FPS on image input.
#   - TEXT output supported on gemini-live-2.5-flash-preview AND
#     gemini-3.1-flash-live-preview (no AUDIO + transcription detour).
#   - Gemini does NOT auto-narrate. To get a description, send a text
#     prompt — model responds against recent frame context.
#   - Sessions hard-cap at ~2 min for audio+video; session_resumption
#     + context_window_compression let us extend indefinitely by
#     reconnecting with a cached handle on server GoAway or socket drop.
#   - 1011 keepalive timeouts are caused by blocking the asyncio loop
#     and missing server pings — separate send/receive tasks, no sync
#     work in either.

_STREAM_PROMPT: str = (
    "Briefly describe what's HAPPENING on screen right now (motion, "
    "current content, app, any visible text or captions). 1-2 short "
    "sentences for a voice assistant to relay. Skip pleasantries."
)


async def _stream_session(session, get_jpeg_fn, resume_handle: Optional[str]) -> Optional[str]:
    """One Gemini Live session lifecycle for streaming-mode observer.

    Opens a Live WebSocket, runs three concurrent tasks against it:
      - frame pusher (1 FPS, send_realtime_input(media=Blob))
      - prompt ticker (every STREAM_PROMPT_INTERVAL_S,
        send_realtime_input(text=...))
      - response receiver (drain receive(), cache text, watch resumption)

    Returns the latest session_resumption handle (for reconnect on
    socket drop), or None if no handle was received.
    """
    global _GLOBAL_LATEST
    from google import genai
    from google.genai import types

    api_key = os.environ.get("GOOGLE_API_KEY", "")
    if not api_key:
        log.warning("[screen-observer:stream] GOOGLE_API_KEY unset — bailing")
        return None

    # Pin the API version to v1beta to match Google's official
    # cookbook quickstart (Get_started_LiveAPI.py); Live preview
    # endpoints live there. Without the pin the SDK can pick a
    # version that doesn't expose Live cleanly.
    client = genai.Client(
        http_options={"api_version": "v1beta"},
        api_key=api_key,
    )

    # context_window_compression keeps the model's working context
    # bounded as frames pile up; session_resumption lets us reconnect
    # after a server-initiated GoAway or socket drop without losing
    # conversational state. media_resolution matches the official
    # cookbook sample — tells Gemini how to size frames it receives.
    cfg = types.LiveConnectConfig(
        response_modalities=["TEXT"],
        media_resolution="MEDIA_RESOLUTION_MEDIUM",
        context_window_compression=types.ContextWindowCompressionConfig(
            trigger_tokens=25600,
            sliding_window=types.SlidingWindow(target_tokens=12800),
        ),
        session_resumption=types.SessionResumptionConfig(handle=resume_handle),
        system_instruction=types.Content(
            role="user",
            parts=[types.Part(text=(
                "You are a continuous screen observer for a voice "
                "assistant. The user streams frames from their screen "
                "at ~1 FPS. When asked, describe what's happening in "
                "1-2 sentences focused on motion, content, app, and "
                "any readable text. Don't ask questions. Don't narrate "
                "that you see frames — speak as if you can simply see "
                "the screen."
            ))],
        ),
    )

    latest_handle = resume_handle

    async with client.aio.live.connect(model=LIVE_MODEL, config=cfg) as live:
        log.info(
            f"[screen-observer:stream] connected ({LIVE_MODEL})"
            + (" — resumed" if resume_handle else "")
        )

        async def push_frames() -> None:
            """Stream JPEG frames at ≤1 FPS via send_realtime_input(media=)."""
            while True:
                jpeg = get_jpeg_fn(max_age_s=STREAM_FRAME_INTERVAL_S * 4.0)
                if jpeg is not None:
                    try:
                        await live.send_realtime_input(
                            media=types.Blob(data=jpeg, mime_type="image/jpeg")
                        )
                    except Exception as e:
                        log.warning(f"[screen-observer:stream] frame send failed: {e}")
                        raise
                await asyncio.sleep(STREAM_FRAME_INTERVAL_S)

        async def tick_prompts() -> None:
            """Every STREAM_PROMPT_INTERVAL_S, ask Gemini to describe the
            current screen. The prompt rides over
            send_realtime_input(text=) so it interleaves with the frame
            stream naturally."""
            # Initial delay so a few frames land first.
            await asyncio.sleep(STREAM_FRAME_INTERVAL_S * 2)
            while True:
                try:
                    await live.send_realtime_input(text=_STREAM_PROMPT)
                except Exception as e:
                    log.warning(f"[screen-observer:stream] prompt send failed: {e}")
                    raise
                await asyncio.sleep(STREAM_PROMPT_INTERVAL_S)

        async def drain_responses() -> None:
            """Pull text + control messages off the socket. Updates the
            cache on each turn_complete; tracks the latest resumption
            handle for the outer reconnect loop."""
            nonlocal latest_handle
            global _GLOBAL_LATEST
            buf: list[str] = []
            answers = 0
            async for msg in live.receive():
                sc = getattr(msg, "server_content", None)
                if sc is not None:
                    mt = getattr(sc, "model_turn", None)
                    if mt is not None and mt.parts:
                        for part in mt.parts:
                            t = getattr(part, "text", None)
                            if t:
                                buf.append(t)
                    if getattr(sc, "turn_complete", False):
                        answers += 1
                        text = "".join(buf).strip()
                        buf.clear()
                        if text:
                            pair = (text, time.monotonic())
                            session._jarvis_latest_screen_description = pair
                            _GLOBAL_LATEST = pair
                            log.info(
                                f"[screen-observer:stream] answer={answers}: "
                                f"{text[:100]}"
                            )
                    # Server-initiated wind-down — exit cleanly so the
                    # outer loop reconnects with the resumption handle
                    # before the socket is yanked.
                    go = getattr(sc, "go_away", None)
                    if go is not None:
                        time_left = getattr(go, "time_left", None)
                        log.info(
                            f"[screen-observer:stream] GoAway received "
                            f"(time_left={time_left}); will reconnect"
                        )
                        return
                # Resumption handle updates — cache for the reconnect.
                sru = getattr(msg, "session_resumption_update", None)
                if sru is not None and getattr(sru, "resumable", False):
                    new_handle = getattr(sru, "new_handle", None)
                    if new_handle:
                        latest_handle = new_handle

        pusher = asyncio.create_task(push_frames(), name="stream-obs-pusher")
        ticker = asyncio.create_task(tick_prompts(), name="stream-obs-ticker")
        drainer = asyncio.create_task(drain_responses(), name="stream-obs-drainer")
        try:
            done, pending = await asyncio.wait(
                {pusher, ticker, drainer},
                return_when=asyncio.FIRST_EXCEPTION,
            )
            for t in done:
                exc = t.exception()
                if exc:
                    raise exc
        finally:
            import contextlib
            for t in (pusher, ticker, drainer):
                if not t.done():
                    t.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await t

    return latest_handle


async def _stream_loop(session) -> None:
    """Long-running streaming-mode supervisor. Keeps a Gemini Live
    WebSocket open; reconnects with the cached session_resumption
    handle on disconnect (server GoAway, socket drop, exception)."""
    try:
        from pipeline.screen_share_sink import latest_jpeg_global
    except Exception as e:
        log.warning(f"[screen-observer:stream] sink import failed: {e}")
        return

    resume_handle: Optional[str] = None
    backoff_s = 1.0
    BACKOFF_MAX_S = 30.0
    while True:
        try:
            handle = await _stream_session(session, latest_jpeg_global, resume_handle)
            if handle:
                resume_handle = handle
            log.info("[screen-observer:stream] session ended; reconnecting")
            backoff_s = 1.0
        except asyncio.CancelledError:
            log.info("[screen-observer:stream] cancelled — exiting")
            raise
        except Exception as e:
            log.warning(
                f"[screen-observer:stream] session error "
                f"({type(e).__name__}: {e}); reconnect in {backoff_s:.1f}s"
            )
            try:
                await asyncio.sleep(backoff_s)
            except asyncio.CancelledError:
                raise
            backoff_s = min(backoff_s * 2, BACKOFF_MAX_S)


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
