"""Partial-word barge-in tap — free, local replacement for the Deepgram
streaming partials that mid-utterance interruption used to need.

Context (2026-07-02): the echo-aware barge-in layer fires on transcript
NOVELTY, but local faster-whisper is non-streaming — finals arrive only
after the user stops talking, so JARVIS talks over the user until their
utterance ends. Deepgram partials fixed that for ~$; this tap does it
for $0: a tiny Vosk streaming recognizer (small-en model, CPU) watches
the mic ONLY while JARVIS is speaking and runs each partial through the
SAME echo-novelty gate. Probe on this box: first partial word ~0.3-0.4 s
after voice onset, 0.28x realtime on one core. Whisper remains the
turn/transcript STT — Vosk text is used solely for the interrupt
decision and never enters chat history.

TRANSPORT (v2, same night): v1 opened a second `rtc.AudioStream` on the
mic track — live fact-check showed it STARVES after ~1 s (first frames
arrive, then silence; the task stays parked in `async for` forever, both
sessions). Secondary track streams do not get sustained frames in this
stack (which also means AcousticTap's feed is suspect — its consumers
tolerate empty data, so nobody noticed). v2 tees frames off the
framework's own STT feed via a `JarvisAgent.stt_node` override — the one
path PROVEN to flow continuously (it feeds whisper all day).
`feed_frame()` is called on the hot audio path: it only enqueues
(bounded queue, drop-new on overflow) — recognition runs in a worker
task; the Vosk model pre-arms via a thread so the event loop never
stalls.

Duty cycle: frames are dropped instantly unless `agent_state ==
"speaking"`. One interrupt per speaking period; recognizer resets
between periods. Echo of JARVIS's own TTS transcribes too — the
echo_gate novelty check filters it, same as the finals-based layer.

Soft dependency: if `vosk` or the model dir is missing, the tap logs
one hint and disables itself — no impact on the voice path.

Enable/disable: JARVIS_PARTIAL_BARGEIN (default 1).
Model dir: JARVIS_PARTIAL_BARGEIN_MODEL
           (default ~/.jarvis/models/vosk-small-en).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger("jarvis.partial_bargein")

_TARGET_RATE = 16000
_QUEUE_MAX = 256  # ~2.5 s of 10 ms frames; overflow drops NEW frames

_RESET = None  # queue sentinel: speaking period ended


def enabled() -> bool:
    return os.environ.get("JARVIS_PARTIAL_BARGEIN", "1") != "0"


def model_dir() -> Path:
    return Path(
        os.environ.get(
            "JARVIS_PARTIAL_BARGEIN_MODEL",
            Path.home() / ".jarvis" / "models" / "vosk-small-en",
        )
    ).expanduser()


def _pcm16_to_16k_mono(data: bytes, sample_rate: int, num_channels: int) -> bytes:
    """Downmix + resample s16le PCM to 16 kHz mono for Vosk.

    numpy-based so it's unit-testable without livekit types. Integer
    ratios (48k/32k) decimate by stride; anything else linear-interps.
    ASR-partial quality is unaffected by the cheap resample.
    """
    import numpy as np

    samples = np.frombuffer(data, dtype=np.int16)
    if num_channels > 1:
        samples = samples.reshape(-1, num_channels).mean(axis=1).astype(np.int16)
    if sample_rate == _TARGET_RATE:
        return samples.tobytes()
    if sample_rate % _TARGET_RATE == 0:
        return samples[:: sample_rate // _TARGET_RATE].tobytes()
    n_out = int(len(samples) * _TARGET_RATE / sample_rate)
    x_out = np.linspace(0, len(samples) - 1, n_out)
    return np.interp(x_out, np.arange(len(samples)), samples).astype(np.int16).tobytes()


class PartialBargeInTap:
    """Consumes mic frames teed off the STT feed; interrupts on novel
    partial words while JARVIS speaks."""

    def __init__(
        self,
        *,
        session,
        on_interrupt: Callable[[str], None],
    ) -> None:
        self._session = session
        self._on_interrupt = on_interrupt
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAX)
        self._task: Optional[asyncio.Task] = None
        self._rec = None            # KaldiRecognizer, armed by the worker
        self._model = None
        self._fired_this_speech = False
        self._was_speaking = False
        self._disabled = not enabled()
        # One-shot INFO diagnostics (2026-07-02 fact-check discipline):
        # localize any stall without debug logging.
        self._frames_seen = 0
        self._logged_first_speaking = False

    # ── lifecycle ───────────────────────────────────────────────────

    def start(self) -> None:
        """Create the recognition worker. Call once from the entrypoint."""
        if self._disabled:
            logger.info("[partial-bargein] disabled via JARVIS_PARTIAL_BARGEIN=0")
            return
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._worker(), name="partial-bargein-worker")

        def _on_done(t: asyncio.Task) -> None:
            if t.cancelled():
                logger.warning("[partial-bargein] worker CANCELLED")
            elif t.exception() is not None:
                logger.warning(f"[partial-bargein] worker DIED: {t.exception()!r}")
            else:
                logger.info("[partial-bargein] worker ended")

        self._task.add_done_callback(_on_done)

    async def _arm(self) -> bool:
        if self._disabled:
            return False
        if self._rec is not None:
            return True
        try:
            from vosk import Model, KaldiRecognizer, SetLogLevel
        except ImportError:
            logger.warning(
                "[partial-bargein] vosk not installed — mid-utterance "
                "barge-in disabled (pip install vosk)"
            )
            self._disabled = True
            return False
        mdir = model_dir()
        if not mdir.is_dir():
            logger.warning(
                f"[partial-bargein] model dir missing ({mdir}) — disabled. "
                "Fetch: https://alphacephei.com/vosk/models "
                "(vosk-model-small-en-us-0.15) and unzip to that path."
            )
            self._disabled = True
            return False
        SetLogLevel(-1)
        # Model load takes ~1 s — off the event loop.
        self._model = await asyncio.to_thread(Model, str(mdir))
        self._rec = KaldiRecognizer(self._model, _TARGET_RATE)
        logger.info(f"[partial-bargein] armed (model={mdir.name}, cpu streaming)")
        return True

    # ── hot path (called from the stt_node tee) ─────────────────────

    def _speaking(self) -> bool:
        return getattr(self._session, "agent_state", "") == "speaking"

    def feed_frame(self, frame) -> None:
        """Cheap, never raises. Enqueues speaking-period frames only."""
        try:
            if self._frames_seen == 0:
                logger.info(
                    f"[partial-bargein] first stt-feed frame "
                    f"(rate={frame.sample_rate} ch={frame.num_channels})"
                )
            self._frames_seen += 1
            if self._frames_seen % 6000 == 0:  # ~60 s of 10 ms frames
                logger.info(
                    f"[partial-bargein] heartbeat: {self._frames_seen} frames, "
                    f"agent_state={getattr(self._session, 'agent_state', '?')!r}"
                )
            if self._disabled:
                return
            if not self._speaking():
                if self._was_speaking:
                    self._was_speaking = False
                    self._queue.put_nowait(_RESET)
                return
            if not self._was_speaking:
                self._was_speaking = True
                if not self._logged_first_speaking:
                    self._logged_first_speaking = True
                    logger.info(
                        "[partial-bargein] first frame during agent speech — engaging"
                    )
            if self._fired_this_speech:
                return
            self._queue.put_nowait(
                (bytes(frame.data), frame.sample_rate, frame.num_channels)
            )
        except asyncio.QueueFull:
            pass
        except Exception as e:
            logger.debug(f"[partial-bargein] feed skipped: {e}")

    # ── decision core (unit-tested) ─────────────────────────────────

    def feed_partial_text(self, partial: str) -> bool:
        """Fire the interrupt for a novel partial once per speaking
        period. Returns True if fired."""
        if self._fired_this_speech or not partial.strip():
            return False
        # Substance guard (2026-07-02, live tuning): the FIRST partial is
        # always one tiny word, and single function words ("the", "i")
        # also come from TV/noise — firing on them makes any room sound
        # cut JARVIS off. Require two words, or one word of >=4 letters
        # ("stop", "wait", "jarvis" still fire instantly on word one).
        words = partial.split()
        if len(words) < 2 and len(words[0]) < 4:
            return False
        try:
            from pipeline import echo_gate, speaking_tracker
            if echo_gate.in_cooldown():
                return False
            if echo_gate.is_echo(
                partial, speaking_tracker.current_speaking_text()
            ):
                return False
            echo_gate.note_bargein()
        except Exception as e:
            logger.debug(f"[partial-bargein] echo check failed: {e}")
            return False  # fail safe — never interrupt on an unverified partial
        self._fired_this_speech = True
        logger.info(f"[partial-bargein] novel partial → interrupt: {partial[:60]!r}")
        try:
            self._on_interrupt(partial)
        except Exception as e:
            logger.warning(f"[partial-bargein] interrupt callback failed: {e}")
        return True

    # ── worker ──────────────────────────────────────────────────────

    async def _worker(self) -> None:
        while True:
            item = await self._queue.get()
            try:
                if item is _RESET:
                    if self._rec is not None:
                        self._rec.Reset()
                    self._fired_this_speech = False
                    continue
                if self._fired_this_speech or not await self._arm():
                    continue
                data, rate, channels = item
                self._rec.AcceptWaveform(_pcm16_to_16k_mono(data, rate, channels))
                partial = json.loads(self._rec.PartialResult()).get("partial", "")
                if partial:
                    self.feed_partial_text(partial)
            except Exception as e:
                logger.debug(f"[partial-bargein] frame skipped: {e}")
