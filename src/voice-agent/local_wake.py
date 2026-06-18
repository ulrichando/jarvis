"""Local wake-listener — keeps voice-wake working while the mic is cut
off from the cloud.

Phase 2 of the silent-mode token-leak fix. While JARVIS is silenced, the
voice-client STOPS publishing mic audio to the SFU (so no audio reaches
Deepgram and no tokens are spent), and feeds raw frames to THIS listener
instead. The listener:

  - segments utterances locally by RMS (start on speech, end after a
    short hangover of silence, or at a max length),
  - transcribes each finished utterance LOCALLY with faster-whisper
    (lazy-loaded, runs in a worker thread — never on the audio thread),
  - and, when the transcript is a wake command ("Jarvis, wake up"),
    clears the silent flag and fires an `on_wake` callback so the
    voice-client can resume publishing + voice a short ack.

Everything stays on the machine: no cloud STT, no cost, no room audio
leaving the box while you believe JARVIS is muted.

Gated by `JARVIS_SILENT_LOCAL_WAKE=1` (default off → `enabled` False →
the voice-client keeps its prior behaviour). The wake/mute vocabulary
comes from `pipeline.voice_commands` — the SAME matcher the agent uses,
so the local path and the agent never disagree on what wakes JARVIS.

Spec: docs/superpowers/specs/2026-06-18-silent-mode-token-leak-fix-design.md
"""
from __future__ import annotations

import asyncio
import collections
import inspect
import io
import logging
import os
import wave
from pathlib import Path
from typing import Awaitable, Callable, Optional

from pipeline.voice_commands import is_wake

logger = logging.getLogger("jarvis.local_wake")


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "0").strip().lower() in ("1", "true", "yes", "on")


class _Segmenter:
    """Turns a stream of ``(rms, pcm_bytes)`` frames into complete
    utterance blobs.

    Starts an utterance on the first speech frame (``rms >= speech_rms``),
    then ends it after ``silence_hangover_s`` of sub-threshold audio — or
    early at ``max_utterance_s`` so a long monologue still gets checked.
    Leading silence is dropped; trailing hangover silence is kept (Whisper
    handles it fine and it guards the last word).
    """

    def __init__(self, *, frame_s: float, speech_rms: float,
                 silence_hangover_s: float, max_utterance_s: float) -> None:
        self._frame_s = frame_s
        self._speech_rms = speech_rms
        self._hangover_frames = max(1, round(silence_hangover_s / frame_s))
        self._max_frames = max(1, round(max_utterance_s / frame_s))
        self._buf: list[bytes] = []
        self._in_utterance = False
        self._silence_run = 0

    def _reset(self) -> None:
        self._buf = []
        self._in_utterance = False
        self._silence_run = 0

    def push(self, rms: float, pcm: bytes) -> Optional[bytes]:
        """Feed one frame. Returns the utterance PCM when one just ended,
        else None."""
        speaking = rms >= self._speech_rms
        if not self._in_utterance:
            if not speaking:
                return None              # drop leading silence
            self._in_utterance = True
            self._buf = [pcm]
            self._silence_run = 0
            # A single speech frame can't end an utterance; keep buffering.
            if len(self._buf) >= self._max_frames:
                seg = b"".join(self._buf)
                self._reset()
                return seg
            return None

        self._buf.append(pcm)
        self._silence_run = 0 if speaking else self._silence_run + 1
        if self._silence_run >= self._hangover_frames or len(self._buf) >= self._max_frames:
            seg = b"".join(self._buf)
            self._reset()
            return seg
        return None


class LocalWakeListener:
    """Owns the local wake path: a thread-safe frame queue fed from the
    audio callback, a background loop that segments + transcribes +
    matches wake phrases, and the silent-flag bookkeeping.

    `transcribe` is injected so the audio/model layer is swappable and
    the logic is testable without faster-whisper. In production it
    defaults to a lazily-loaded faster-whisper model.
    """

    def __init__(
        self,
        *,
        silent_file: Path,
        on_wake: Callable[[str], object],
        transcribe: Optional[Callable[[bytes], Awaitable[str]]] = None,
        enabled: Optional[bool] = None,
        sample_rate: int = 48000,
        frame_s: float = 0.01,
        speech_rms: Optional[float] = None,
        silence_hangover_s: float = 0.6,
        max_utterance_s: float = 6.0,
        poll_interval_s: float = 0.3,
        max_queue_frames: int = 4000,
    ) -> None:
        self._silent_file = Path(silent_file)
        self._on_wake = on_wake
        self._transcribe = transcribe or self._default_transcribe
        self._enabled = _env_flag("JARVIS_SILENT_LOCAL_WAKE") if enabled is None else enabled
        self._sample_rate = sample_rate
        self._poll_interval_s = poll_interval_s
        if speech_rms is None:
            speech_rms = float(os.environ.get("JARVIS_SILENT_WAKE_RMS", "300") or "300")
        self._seg = _Segmenter(
            frame_s=frame_s, speech_rms=speech_rms,
            silence_hangover_s=silence_hangover_s, max_utterance_s=max_utterance_s,
        )
        self._queue: "collections.deque[tuple[float, bytes]]" = collections.deque(
            maxlen=max_queue_frames
        )
        self._active = False
        # Lazy faster-whisper (production path only).
        self._model = None
        self._load_lock = asyncio.Lock()
        self._model_size = os.environ.get("JARVIS_SILENT_WAKE_MODEL", "small").strip() or "small"
        self._device = os.environ.get("JARVIS_SILENT_WAKE_DEVICE", "cpu").strip() or "cpu"
        self._compute = os.environ.get("JARVIS_SILENT_WAKE_COMPUTE", "int8").strip() or "int8"

    # ── State ─────────────────────────────────────────────────────────
    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def active(self) -> bool:
        """Cheap read for the audio callback: True when we should capture
        locally (enabled AND silenced)."""
        return self._active

    def refresh(self) -> None:
        """Recompute `active` from the silent flag file. Called by the run
        loop on its own cadence so the audio thread only reads a cached
        bool (no per-frame stat syscall)."""
        try:
            self._active = self._enabled and self._silent_file.exists()
        except Exception:
            self._active = False

    # ── Audio-thread entry ────────────────────────────────────────────
    def feed(self, rms: float, pcm: bytes) -> None:
        """Append one mic frame. Called from the PortAudio callback —
        MUST stay cheap (a bounded deque append; transcription happens off
        this thread). No-op unless active, so when JARVIS isn't silenced
        this costs one bool check."""
        if not self._active:
            return
        self._queue.append((rms, pcm))

    # ── Background processing ─────────────────────────────────────────
    async def _consume_once(self) -> None:
        """Drain queued frames through the segmenter; transcribe + wake-
        check each finished utterance. Safe to call repeatedly."""
        while self._queue:
            try:
                rms, pcm = self._queue.popleft()
            except IndexError:
                break
            seg = self._seg.push(rms, pcm)
            if seg is None:
                continue
            try:
                text = await self._transcribe(seg)
            except Exception as e:
                logger.debug("[local-wake] transcribe failed: %s", e)
                continue
            if text and is_wake(text):
                logger.info("[local-wake] wake phrase heard locally: %r", text[:80])
                self._clear_silent()
                await self._fire_wake(text)
                return

    def _clear_silent(self) -> None:
        try:
            self._silent_file.unlink(missing_ok=True)
        except Exception as e:
            logger.debug("[local-wake] could not clear silent flag: %s", e)
        self._active = False
        self._queue.clear()

    async def _fire_wake(self, text: str) -> None:
        try:
            res = self._on_wake(text)
            if inspect.isawaitable(res):
                await res
        except Exception as e:
            logger.warning("[local-wake] on_wake callback failed: %s", e)

    async def run(self) -> None:
        """Background loop: refresh active-state from the file, drain the
        queue. Cheap no-op while not silenced."""
        logger.info(
            "[local-wake] listener started (enabled=%s, model=%s/%s)",
            self._enabled, self._model_size, self._device,
        )
        try:
            while True:
                self.refresh()
                if self._active:
                    await self._consume_once()
                await asyncio.sleep(self._poll_interval_s)
        except asyncio.CancelledError:
            raise

    # ── Default (production) transcriber ──────────────────────────────
    async def _ensure_model(self):
        if self._model is not None:
            return self._model
        async with self._load_lock:
            if self._model is None:
                def _load():
                    from faster_whisper import WhisperModel
                    return WhisperModel(
                        self._model_size, device=self._device, compute_type=self._compute,
                    )
                logger.info("[local-wake] loading faster-whisper model=%s", self._model_size)
                self._model = await asyncio.to_thread(_load)
        return self._model

    def _pcm_to_wav(self, pcm: bytes) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)            # int16
            w.setframerate(self._sample_rate)
            w.writeframes(pcm)
        return buf.getvalue()

    async def _default_transcribe(self, pcm: bytes) -> str:
        model = await self._ensure_model()
        wav = self._pcm_to_wav(pcm)

        def _run():
            segments, _info = model.transcribe(
                io.BytesIO(wav), beam_size=1, vad_filter=False, language="en",
            )
            return "".join(seg.text for seg in segments).strip()

        return await asyncio.to_thread(_run)
