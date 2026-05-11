"""LiveKit screen-share publisher for the voice client.

When the user asks JARVIS to look at the screen (tray toggle or voice
"share my screen"), this module captures the X11 root window via ffmpeg
and publishes it as a LiveKit video track. The agent subscribes on the
other side and feeds the freshest frame into the vision-backend, so
'what's on my screen' returns in ~100 ms instead of paying scrot+PNG
encode every call.

Design:
  - ffmpeg `-f x11grab` is the capture source. PortAudio-free, no Wayland
    PipeWire dependency, runs on the same X11 display the user is on.
  - Output format is raw YUV420p (`-pix_fmt yuv420p -f rawvideo`).
    YUV420p is byte-for-byte compatible with LiveKit's I420 buffer type,
    so we hand frames to `VideoSource.capture_frame` without colour
    conversion.
  - 3 fps / 1280x800 by default. The vision use-case is "describe what
    you see" — we don't need 60 fps and we don't want to flood the SFU's
    loopback. Both are tunable via env (JARVIS_SCREEN_SHARE_FPS /
    _WIDTH / _HEIGHT) for the edge cases.
  - OFF by default. Capturing the desktop has privacy implications;
    require an explicit toggle (HTTP /screen-share or the tray button
    that calls it).

Lifecycle:
  - `start(room)` is idempotent — calling it twice is a no-op.
  - `stop()` always succeeds: kills ffmpeg, unpublishes the track, joins
    the read loop. Safe to call even if not started.
  - When the room disconnects, the voice-client's run_once teardown
    calls stop() so we don't leak the subprocess across reconnects.

Why not the LiveKit `screen_share` plugin: the Python rtc package
doesn't ship a built-in desktop capturer (only AudioSource/VideoSource
primitives). The ffmpeg approach gives us full control over fps,
resolution, and pixel format — and uses tools already on the box.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from livekit import rtc


__all__ = ["ScreenShare"]


log = logging.getLogger("jarvis.voice_client.screen_share")


# Capture defaults — chosen for 'describe what's on screen' workloads,
# not real-time gaming. Override at process start via env.
WIDTH: int   = int(os.environ.get("JARVIS_SCREEN_SHARE_WIDTH",  "1280"))
HEIGHT: int  = int(os.environ.get("JARVIS_SCREEN_SHARE_HEIGHT", "800"))
# Default 1 fps (dropped from 3 on 2026-05-11 evening). The Gemini Live
# API re-bills the full context window per turn, so each extra frame
# inflates per-query cost roughly linearly. 1 fps matches LiveKit's
# `video_sampler` default and is what AI Studio's Stream realtime uses
# internally. Bump via JARVIS_SCREEN_SHARE_FPS=3 if you need sharper
# motion capture (computer_use action loops, demos).
FPS: int     = int(os.environ.get("JARVIS_SCREEN_SHARE_FPS",    "1"))
DISPLAY: str = os.environ.get("JARVIS_SCREEN_SHARE_DISPLAY",    os.environ.get("DISPLAY", ":0"))

# I420 = YUV420p planar = W*H bytes Y + (W*H/4) bytes U + (W*H/4) bytes V
# = W * H * 3/2 bytes per frame.
_FRAME_BYTES: int = (WIDTH * HEIGHT * 3) // 2


class ScreenShare:
    """Single-screen X11 → LiveKit video publisher.

    Owns one ffmpeg subprocess + one LiveKit video track. State is
    contained — no module-level mutables. Lifecycle is `start(room)` /
    `stop()`; both can be called repeatedly without bookkeeping.
    """

    def __init__(self) -> None:
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._source: Optional[rtc.VideoSource]         = None
        self._track: Optional[rtc.LocalVideoTrack]      = None
        self._pub: Optional[rtc.LocalTrackPublication]  = None
        self._reader_task: Optional[asyncio.Task]       = None
        self._room: Optional[rtc.Room]                  = None
        self._lock = asyncio.Lock()

    def is_active(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def start(self, room: rtc.Room) -> None:
        """Spawn ffmpeg + publish the video track. Idempotent."""
        async with self._lock:
            if self.is_active():
                log.debug("[screen-share] already active — start() is a no-op")
                return

            # Open ffmpeg first; if it fails, we never touch the room.
            cmd = [
                "ffmpeg",
                "-hide_banner", "-loglevel", "error",
                "-f", "x11grab",
                "-framerate", str(FPS),
                "-video_size", f"{WIDTH}x{HEIGHT}",
                "-i", DISPLAY,
                "-pix_fmt", "yuv420p",
                "-f", "rawvideo",
                "pipe:1",
            ]
            log.info(f"[screen-share] starting ffmpeg: {' '.join(cmd)}")
            try:
                self._proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except FileNotFoundError:
                log.error("[screen-share] ffmpeg not on PATH; install it or set JARVIS_SCREEN_SHARE_FFMPEG")
                self._proc = None
                raise

            # Create + publish the LiveKit track. is_screencast=True
            # tags the source so the SFU + clients can render it
            # correctly (no mirroring, no smoothing). SOURCE_SCREENSHARE
            # is what the subscribed agent filters on.
            self._source = rtc.VideoSource(WIDTH, HEIGHT, is_screencast=True)
            self._track = rtc.LocalVideoTrack.create_video_track("screen", self._source)
            self._pub = await room.local_participant.publish_track(
                self._track,
                rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_SCREENSHARE),
            )
            self._room = room
            self._reader_task = asyncio.create_task(
                self._read_frames(), name="screen-share-reader",
            )
            log.info(f"[screen-share] published — {WIDTH}x{HEIGHT}@{FPS}fps from {DISPLAY}")

    async def stop(self) -> None:
        """Kill ffmpeg + unpublish track. Always safe to call."""
        async with self._lock:
            if self._proc is not None:
                try:
                    self._proc.terminate()
                    try:
                        await asyncio.wait_for(self._proc.wait(), timeout=2.0)
                    except asyncio.TimeoutError:
                        log.warning("[screen-share] ffmpeg didn't exit on SIGTERM, killing")
                        self._proc.kill()
                        await self._proc.wait()
                except ProcessLookupError:
                    pass
                except Exception as e:
                    log.warning(f"[screen-share] ffmpeg teardown error: {e}")
                self._proc = None

            if self._reader_task is not None:
                self._reader_task.cancel()
                try:
                    await self._reader_task
                except (asyncio.CancelledError, Exception):
                    pass
                self._reader_task = None

            if self._pub is not None and self._room is not None:
                try:
                    await self._room.local_participant.unpublish_track(self._pub.sid)
                except Exception as e:
                    log.debug(f"[screen-share] unpublish skipped: {e}")
                self._pub = None

            self._track = None
            self._source = None
            self._room = None
            log.info("[screen-share] stopped")

    async def _read_frames(self) -> None:
        """Pump raw I420 frames from ffmpeg into the LiveKit source.

        ffmpeg writes frames back-to-back with no header — each block of
        exactly `_FRAME_BYTES` is one frame. We `readexactly` per frame
        so a partial read at shutdown raises and we exit cleanly.
        """
        proc = self._proc
        source = self._source
        if proc is None or source is None or proc.stdout is None:
            return
        try:
            while True:
                buf = await proc.stdout.readexactly(_FRAME_BYTES)
                # `data` accepts bytes / bytearray / memoryview. Pass the
                # bytes object directly; the LiveKit FFI copies it.
                frame = rtc.VideoFrame(
                    WIDTH,
                    HEIGHT,
                    rtc.VideoBufferType.I420,
                    buf,
                )
                # capture_frame accepts a 0 timestamp (LiveKit stamps it
                # internally). Avoids us having to track monotonic time.
                source.capture_frame(frame)
        except asyncio.IncompleteReadError:
            log.info("[screen-share] ffmpeg pipe closed — reader exiting")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning(f"[screen-share] frame read error: {e}")
            # Drain stderr so the user can see ffmpeg's complaint in the log.
            if proc.stderr is not None:
                try:
                    err = await proc.stderr.read()
                    if err:
                        log.warning(f"[screen-share] ffmpeg stderr: {err.decode('utf-8', 'ignore').strip()}")
                except Exception:
                    pass
