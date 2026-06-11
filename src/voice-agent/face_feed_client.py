"""Push kiosk-face inputs (audio level + spoken transcript) into the
voice-client's ``POST /face/feed`` endpoint from a *separate* process.

Why this exists
---------------
The Claude voice path runs inside the LiveKit ``AgentSession``: the
voice-client sees the agent's audio track (→ ``output_level``) and the
``lk.transcription`` stream (→ viseme/expression text), so it drives the kiosk
face itself. The realtime speech-to-speech modes —
``bin/jarvis-gpt-tools`` (OpenAI Realtime) and ``bin/jarvis-gemini-tools``
(Gemini Live) — run as standalone processes that play their own audio via
``paplay`` and never touch LiveKit, so the face got *nothing* and froze (mouth
+ eyes dead; only the Claude provider animated).

This pusher bridges the gap: the realtime script feeds raw output-audio chunks
+ transcript here; we compute per-sub-frame RMS and POST
``{text, level, speaking}`` to the voice-client's face server, which runs the
SAME viseme + expression engines the Claude path uses. The kiosk renderer is
unchanged — it keeps polling ``/face`` + ``/level``.

The playback clock (the part that makes lips sync)
--------------------------------------------------
Realtime APIs deliver output audio FASTER than realtime — a 10 s reply can
arrive in a ~2 s network burst, and even with stdin backpressure the OS pipe
plus paplay's buffer keep playback as much as a few seconds behind the bytes
we just received. Computing the face level at *receive* time therefore flaps
the mouth ahead of the voice and closes it while JARVIS is still talking
(worse: the viseme engine treats the early ``speaking=False`` falling edge as
end-of-utterance and resets, blanking the face mid-reply).

So ``feed_audio`` never reports a level directly. It slices each chunk into
~20 ms sub-frames and schedules ``(play_at, rms)`` entries on a virtual
playback clock: the first chunk of an utterance is assumed audible
``start_latency_s`` from now (pipe fill + paplay's --latency-msec=80), and
every subsequent chunk plays back-to-back after it. The background loop pops
entries as their play time arrives and POSTs *those* levels —
``speaking`` stays true until the scheduled playhead drains, exactly
mirroring what the speakers are doing.

Design
------
* Fire-and-forget over a daemon thread + stdlib ``urllib`` — never blocks the
  realtime audio loop and never raises into it. A dropped POST is just one
  skipped face frame.
* POSTs at a fixed cadence (default 30 Hz) while the playhead is live, then a
  short closing burst so the final "mouth closed" frame lands, then idles
  silently (zero traffic between utterances).
* ``hold_s`` keeps ``speaking=True`` across short intra-reply gaps (mirrors
  the voice-client's ``_SPEAKING_HOLD_S``) so the engines don't falling-edge
  reset between back-to-back sentences of one reply.
* ``flush()`` is for callers that discard their playback queue on barge-in:
  it drops the schedule so the face stops with the audio.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.request
from collections import deque

import numpy as np

log = logging.getLogger("jarvis.face_feed")

_DEFAULT_URL = os.environ.get(
    "JARVIS_FACE_FEED_URL", "http://127.0.0.1:8767/face/feed"
)

# Approximate output-path latency: how long after we hand the FIRST chunk of
# an utterance to the player before it is audible (pipe fill + paplay's
# --latency-msec=80 target + scheduling). Only affects utterance START
# alignment; within an utterance the clock chains chunk durations.
_DEFAULT_START_LATENCY_S = float(os.environ.get("JARVIS_FACE_FEED_LATENCY_S", "0.15"))
# Keep `speaking` true this long past the last audible sub-frame, mirroring
# the voice-client's _SPEAKING_HOLD_S — the viseme engine hard-resets on the
# speaking falling edge, so a short inter-sentence gap must not blank the face.
_DEFAULT_HOLD_S = float(os.environ.get("JARVIS_FACE_FEED_HOLD_S", "1.2"))
# Sub-frame granularity of the level envelope. 20 ms ≈ the per-frame RMS the
# Claude playback loop feeds the engines.
_SUBFRAME_S = 0.02
# Hard cap on queued schedule entries (2 min of audio at 20 ms) — a runaway
# feeder degrades to a stale mouth, never to unbounded memory.
_MAX_QUEUED_SUBFRAMES = 6000


def rms_from_pcm16(pcm: bytes) -> float:
    """Normalized RMS (~0..0.3 for speech) of signed-16-bit mono PCM.

    Mirrors the playback loop's ``sqrt(mean(pcm**2)) / 32768`` so the level
    scale matches the Claude path (the viseme engine's ``_RMS_FULL = 0.18`` is
    tuned to it). Empty / odd-length buffers → ``0.0``.
    """
    if not pcm or len(pcm) < 2:
        return 0.0
    if len(pcm) % 2:               # drop a trailing odd byte so frombuffer is happy
        pcm = pcm[:-1]
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(samples ** 2))) / 32768.0


class FaceFeedPusher:
    """Background pusher: realtime script feeds audio+text, we POST face state
    on a playback clock (see module docstring).

    Usage from the realtime audio loop::

        face = FaceFeedPusher(sample_rate=SPK_SAMPLE_RATE)
        ...
        face.feed_audio(pcm_bytes)   # each output-audio chunk, as SENT to the player
        face.feed_text(transcript)   # each transcript delta (accumulated)
        face.reset_text()            # at each new-utterance boundary
        face.flush()                 # only if the playback queue is discarded
    """

    def __init__(
        self,
        url: str = _DEFAULT_URL,
        hz: float = 30.0,
        sample_rate: int = 24000,
        start_latency_s: float | None = None,
        hold_s: float | None = None,
        tail_s: float = 0.3,
    ) -> None:
        self._url = url
        self._period = 1.0 / max(1.0, hz)
        self._bps = max(1, int(sample_rate)) * 2          # mono s16le bytes/sec
        self._subframe_bytes = max(2, (int(self._bps * _SUBFRAME_S) // 2) * 2)
        self._start_latency = (
            _DEFAULT_START_LATENCY_S if start_latency_s is None else max(0.0, start_latency_s)
        )
        self._hold_s = _DEFAULT_HOLD_S if hold_s is None else max(0.0, hold_s)
        # Closing burst: how many speaking=False frames to POST after the
        # playhead + hold drain, so the mouth-closed frame reliably lands.
        self._closing_ticks = max(2, int(tail_s / self._period))
        self._lock = threading.Lock()
        self._frames: deque[tuple[float, float]] = deque()  # (play_end_ts, level)
        self._playhead_end = 0.0   # monotonic ts when ALL queued audio has played
        self._last_audible = 0.0   # monotonic ts of the last tick inside the playhead
        self._cur_level = 0.0
        self._text = ""
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="face-feed-pusher", daemon=True
        )
        self._thread.start()

    # ── called from the realtime audio / transcript loop ────────────────
    def feed_audio(self, pcm: bytes) -> None:
        """Schedule one output-audio chunk (raw s16le mono PCM bytes, exactly
        what was written to the player) onto the playback clock."""
        if not pcm or len(pcm) < 2:
            return
        now = time.monotonic()
        with self._lock:
            # Chain onto the existing schedule; a drained schedule means a
            # fresh utterance starting ~start_latency from now.
            t = self._playhead_end if self._playhead_end > now else now + self._start_latency
            step = self._subframe_bytes
            for off in range(0, len(pcm), step):
                sub = pcm[off:off + step]
                if len(sub) < 2:
                    break
                t += (len(sub) - (len(sub) % 2)) / self._bps
                self._frames.append((t, rms_from_pcm16(sub)))
            self._playhead_end = t
            while len(self._frames) > _MAX_QUEUED_SUBFRAMES:
                self._frames.popleft()

    def feed_text(self, text: str) -> None:
        """Set the latest (accumulated) spoken transcript for this utterance."""
        with self._lock:
            self._text = text or ""

    def reset_text(self) -> None:
        """New-utterance boundary — clear the accumulated transcript so the
        viseme engine doesn't treat the next utterance as a continuation."""
        with self._lock:
            self._text = ""

    def flush(self) -> None:
        """Barge-in: the caller discarded its playback queue — drop the face
        schedule too, so the mouth stops with the audio (the hold is killed;
        the next tick posts the closing speaking=False frames)."""
        with self._lock:
            self._frames.clear()
            self._playhead_end = 0.0
            self._last_audible = 0.0
            self._cur_level = 0.0
            self._text = ""

    def close(self) -> None:
        self._stop.set()

    # ── background loop ─────────────────────────────────────────────────
    def _run(self) -> None:
        closing_left = 0
        while not self._stop.wait(self._period):
            now = time.monotonic()
            with self._lock:
                # Consume every sub-frame whose play time has arrived.
                levels = []
                while self._frames and self._frames[0][0] <= now:
                    levels.append(self._frames.popleft()[1])
                audible = now < self._playhead_end
                if levels:
                    # max over the popped window keeps syllable peaks alive
                    # at the (coarser) POST cadence; the ticker smooths.
                    self._cur_level = max(levels)
                if audible:
                    self._last_audible = now
                else:
                    self._cur_level = 0.0
                speaking = audible or (
                    self._last_audible > 0.0
                    and (now - self._last_audible) < self._hold_s
                )
                level = self._cur_level if audible else 0.0
                text = self._text
            if speaking:
                closing_left = self._closing_ticks
            elif closing_left > 0:
                closing_left -= 1
            else:
                continue            # idle — no traffic between utterances
            self._post({
                "text": text,
                "level": round(level, 4),
                "speaking": speaking,
            })

    def _post(self, payload: dict) -> None:
        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                self._url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=0.5).close()
        except Exception as e:
            # Voice-client may be down / restarting; a skipped frame is fine.
            log.debug(f"[face-feed] POST failed: {e}")
