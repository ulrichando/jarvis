"""Local faster-whisper STT — offline last-resort speech-to-text.

A custom livekit :class:`stt.STT` that runs OpenAI Whisper locally via
faster-whisper (ctranslate2). It is the FINAL rung of JARVIS's STT
FallbackAdapter chain (Deepgram → Groq Whisper → THIS), activated only
when ``JARVIS_LOCAL_STT_ENABLED=1``.

Non-streaming (finals only) — the chain's ``StreamAdapter`` + Silero VAD
wraps it for streaming compatibility, exactly like Groq Whisper Turbo.
Runs CPU/int8 by default so it never contends with the local LLM for the
6 GB GPU and needs no cuDNN; override via ``JARVIS_LOCAL_STT_DEVICE`` /
``JARVIS_LOCAL_STT_COMPUTE`` on a bigger box.

The model loads lazily on first transcription (downloads from HF the
first time, then cached under ~/.cache/huggingface). Part of the local
offline fallback stack — see ``pipeline/config.py`` and the 2026-06-15
local-LLM design (~/.claude/plans/we-need-to-find-polymorphic-allen.md).
"""
from __future__ import annotations

import asyncio
import io
import logging
import os

from livekit import rtc
from livekit.agents import APIConnectionError, APIConnectOptions, stt
from livekit.agents.types import NOT_GIVEN, NotGivenOr
from livekit.agents.utils import AudioBuffer, is_given

logger = logging.getLogger("jarvis.stt.local")


class FasterWhisperSTT(stt.STT):
    """Local Whisper (faster-whisper) as a non-streaming livekit STT."""

    def __init__(
        self,
        *,
        model: str = "large-v3",
        device: str = "cpu",
        compute_type: str = "int8",
        language: str | None = None,
    ) -> None:
        super().__init__(
            capabilities=stt.STTCapabilities(streaming=False, interim_results=False)
        )
        self._model_size = model
        self._device = device
        self._compute_type = compute_type
        self._language = language or None
        self._model = None  # lazy-loaded WhisperModel
        self._load_lock = asyncio.Lock()

    @property
    def label(self) -> str:
        return f"local:faster-whisper/{self._model_size}"

    async def _ensure_model(self):
        if self._model is not None:
            return self._model
        async with self._load_lock:
            if self._model is None:
                def _load():
                    from faster_whisper import WhisperModel
                    return WhisperModel(
                        self._model_size,
                        device=self._device,
                        compute_type=self._compute_type,
                    )
                logger.info(
                    "[stt.local] loading faster-whisper model=%s device=%s compute=%s",
                    self._model_size, self._device, self._compute_type,
                )
                self._model = await asyncio.to_thread(_load)
        return self._model

    async def _recognize_impl(
        self,
        buffer: AudioBuffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions,
    ) -> stt.SpeechEvent:
        lang = (language if is_given(language) else self._language) or None
        # WAV bytes at the source sample rate; faster-whisper decodes +
        # resamples to 16k internally, so no manual resampling is needed.
        wav = rtc.combine_audio_frames(buffer).to_wav_bytes()
        try:
            model = await self._ensure_model()

            def _transcribe():
                segments, info = model.transcribe(
                    io.BytesIO(wav),
                    language=lang,
                    beam_size=1,        # fast; this is a last-resort rung
                    vad_filter=False,   # the chain's Silero VAD already gated this audio
                )
                text = "".join(seg.text for seg in segments).strip()
                return text, getattr(info, "language", None)

            text, detected = await asyncio.to_thread(_transcribe)
        except Exception as e:  # surface as a chain-cascadable error
            raise APIConnectionError(f"faster-whisper local STT failed: {e}") from e

        return stt.SpeechEvent(
            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
            alternatives=[stt.SpeechData(text=text, language=detected or lang or "en")],
        )


def build_local_stt() -> FasterWhisperSTT | None:
    """Construct the local STT rung from env, or None when disabled.

    Gated on ``JARVIS_LOCAL_STT_ENABLED=1``. Defaults: large-v3 on
    CPU/int8 (robust, no GPU/cuDNN dependency, fine for a last-resort
    rung). ``device=auto`` is coerced to ``cpu`` to avoid VRAM
    contention with the local LLM + cuDNN requirements; set
    ``JARVIS_LOCAL_STT_DEVICE=cuda`` explicitly on a box set up for it.
    """
    if os.environ.get("JARVIS_LOCAL_STT_ENABLED", "0") != "1":
        return None
    model = os.environ.get("JARVIS_LOCAL_STT_MODEL", "large-v3").strip() or "large-v3"
    device = os.environ.get("JARVIS_LOCAL_STT_DEVICE", "cpu").strip() or "cpu"
    compute = os.environ.get("JARVIS_LOCAL_STT_COMPUTE", "int8").strip() or "int8"
    if device == "auto":
        device = "cpu"
    try:
        inst = FasterWhisperSTT(model=model, device=device, compute_type=compute)
        logger.info(
            "[stt.local] faster-whisper rung armed: model=%s device=%s compute=%s",
            model, device, compute,
        )
        return inst
    except Exception as e:
        logger.warning("[stt.local] faster-whisper construction failed: %s", e)
        return None
