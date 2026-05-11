"""Per-turn acoustic prosody tap.

Subscribes to the user's audio track on the LiveKit room, decodes
incoming PCM frames, and maintains a rolling buffer of (timestamp,
RMS dB) samples. The agent queries `mean_rms_db(start, end)` after
each turn — using the same VAD-state-change timestamps already
captured for `compute_speech_rate` — and feeds the result into
`AudioMeta.rms_db`.

Why a separate tap rather than reading from the agent's own VAD:
livekit-agents consumes the VAD's `END_OF_SPEECH` events through a
private `_event_ch` that doesn't expose `frames` to user listeners.
Subscribing a parallel `rtc.AudioStream` on the same track is the
clean public API — the audio decode work duplicates, but at one frame
per ~10ms of int16 PCM the cost is negligible (<1% CPU at 48kHz).

Numpy is the only acoustic dep — already pulled in transitively by
livekit's audio plumbing, so no new requirement.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from collections import deque
from dataclasses import dataclass

import numpy as np
from livekit import rtc

logger = logging.getLogger("jarvis.prosody")


__all__ = ["AcousticTap"]


# Floor for log10 conversion. Silence rounds to -80 dB rather than -inf.
_DB_FLOOR = -80.0

# Hold ~8 seconds of per-frame RMS samples — generous so even slow turns
# fit. At ~10ms frames that's 800 entries; at 40ms frames it's 200.
_BUFFER_MAXLEN = 1024


@dataclass(slots=True)
class _RmsSample:
    timestamp: float  # time.monotonic() at frame consumption
    rms_db: float


class AcousticTap:
    """Captures per-frame RMS energy from a participant's audio track."""

    def __init__(self) -> None:
        self._samples: deque[_RmsSample] = deque(maxlen=_BUFFER_MAXLEN)
        self._stream: rtc.AudioStream | None = None
        self._task: asyncio.Task | None = None
        self._attached_to_identity: str | None = None

    def attach_to_room(
        self, room: rtc.Room, *, exclude_identity_prefix: str = "agent-"
    ) -> None:
        """Subscribe to the first non-agent audio track that joins.

        We attach to the FIRST eligible participant we see — a typical
        JARVIS room has one user + one agent, so this is unambiguous.
        Reattaching to a second participant later is a noop.
        """

        @room.on("track_subscribed")
        def _on_track_subscribed(track, publication, participant) -> None:
            try:
                if track.kind != rtc.TrackKind.KIND_AUDIO:
                    return
                if (participant.identity or "").startswith(exclude_identity_prefix):
                    return
                if self._task is not None:
                    return  # already attached
                logger.info(
                    "[acoustic-tap] attaching to %s (track=%s)",
                    participant.identity,
                    publication.sid if publication else "?",
                )
                self._stream = rtc.AudioStream(track)
                self._attached_to_identity = participant.identity
                self._task = asyncio.create_task(
                    self._consume(), name="acoustic-tap"
                )
            except Exception as e:
                logger.warning("[acoustic-tap] subscribe failed: %s", e)

    async def _consume(self) -> None:
        """Drain the audio stream, computing RMS per frame."""
        assert self._stream is not None
        frames_seen = 0
        try:
            async for ev in self._stream:
                frame = getattr(ev, "frame", None)
                if frame is None:
                    continue
                data = getattr(frame, "data", None)
                if not data or len(data) < 2:
                    continue
                # PCM is int16 little-endian. Convert to float32 in [-1, 1].
                pcm = np.frombuffer(data, dtype=np.int16).astype(np.float32)
                if pcm.size == 0:
                    continue
                pcm /= 32768.0
                # RMS in linear amplitude → dB.
                rms = float(np.sqrt(np.mean(pcm * pcm)))
                rms_db = max(_DB_FLOOR, 20.0 * math.log10(rms + 1e-10))
                self._samples.append(_RmsSample(time.monotonic(), rms_db))
                frames_seen += 1
                if frames_seen == 1:
                    logger.debug("[acoustic-tap] first frame received")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("[acoustic-tap] consume errored: %s", e)
        finally:
            logger.debug("[acoustic-tap] consume ended (frames=%d)", frames_seen)

    def mean_rms_db(self, start: float, end: float) -> float:
        """Return the mean RMS dB over the [start, end] monotonic window.

        Returns 0.0 (treat as 'unknown' upstream) if no samples fell in
        the window — the user's mic was muted, the tap hadn't started
        yet, or the timestamps were off.
        """
        if end <= start:
            return 0.0
        rs = [s.rms_db for s in self._samples if start <= s.timestamp <= end]
        if not rs:
            return 0.0
        return float(np.mean(rs))

    def shutdown(self) -> None:
        """Cancel the consume task on session teardown."""
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None
