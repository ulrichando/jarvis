"""CPU faster-whisper STT for the headless VPS voice agent.

Adapted from the desktop agent's providers/faster_whisper_stt.py, with
the GPU/CUDA retry machinery removed — this box is CPU-only by design
(4 vCPU Hetzner VPS). Defaults to whisper `base.en` int8, which
transcribes a short utterance in well under a second on 4 threads;
override with VOICE_STT_MODEL=small.en for better accuracy at ~2-3x
the latency.

Non-streaming (finals only): AgentSession wraps it in a StreamAdapter
with the session's Silero VAD automatically, same as the desktop agent.
The model loads in the worker prewarm hook (and is pre-downloaded into
the docker image), so first-turn latency doesn't pay the download/load
cost.
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

logger = logging.getLogger("voice-agent.stt")

DEFAULT_MODEL = "base.en"


class FasterWhisperSTT(stt.STT):
    """Local Whisper (faster-whisper, CPU int8) as a non-streaming livekit STT."""

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        language: str | None = "en",
        cpu_threads: int = 0,
    ) -> None:
        super().__init__(
            capabilities=stt.STTCapabilities(streaming=False, interim_results=False)
        )
        self._model_size = model
        self._language = language or None
        self._cpu_threads = cpu_threads
        self._model = None  # lazy-loaded WhisperModel
        self._load_lock = asyncio.Lock()

    @property
    def label(self) -> str:
        return f"local:faster-whisper/{self._model_size}"

    def preload(self) -> None:
        """Synchronous model load — for the worker prewarm hook, which
        runs before the job process event loop exists."""
        if self._model is None:
            from faster_whisper import WhisperModel

            logger.info(
                "[stt] preloading faster-whisper model=%s (cpu/int8)",
                self._model_size,
            )
            self._model = WhisperModel(
                self._model_size,
                device="cpu",
                compute_type="int8",
                cpu_threads=self._cpu_threads,
            )
            logger.info("[stt] model preloaded")

    async def ensure_model(self):
        """Load (or return) the ctranslate2 model. Safe to call from prewarm."""
        if self._model is not None:
            return self._model
        async with self._load_lock:
            if self._model is None:
                def _load():
                    from faster_whisper import WhisperModel

                    return WhisperModel(
                        self._model_size,
                        device="cpu",
                        compute_type="int8",
                        cpu_threads=self._cpu_threads,
                    )

                logger.info(
                    "[stt] loading faster-whisper model=%s (cpu/int8)",
                    self._model_size,
                )
                self._model = await asyncio.to_thread(_load)
                logger.info("[stt] model loaded")
        return self._model

    def _transcribe_sync(self, model, wav: bytes, lang: str | None):
        segments, info = model.transcribe(
            io.BytesIO(wav),
            language=lang,
            beam_size=1,  # greedy — latency matters more than the last WER point
            # Whisper hallucinates confident text on non-speech audio (a cough,
            # breathing, background TV/voices) — a hands-free open mic feeds it
            # plenty, and each hallucination lands as its own chat bubble. Defence
            # in depth on top of the session's Silero endpointing:
            #   - vad_filter: whisper's own VAD trims non-speech spans per clip
            #   - no_speech / log_prob / compression thresholds: whisper's built-in
            #     "this isn't speech" gates
            #   - condition_on_previous_text=False: each clip is independent, so a
            #     hallucination can't seed a loop ("Thank you. Thank you. …")
            vad_filter=True,
            no_speech_threshold=0.6,
            log_prob_threshold=-1.0,
            compression_ratio_threshold=2.4,
            condition_on_previous_text=False,
        )
        # Final guard: drop any segment whisper itself is unsure is speech. This
        # is what kills the ambient "Okay.", "you", "It's not beautiful" bubbles
        # that slip past the combined-threshold rule above. If everything is
        # dropped, text == "" and the agent persists no turn.
        kept = [seg.text for seg in segments if seg.no_speech_prob < 0.6]
        text = "".join(kept).strip()
        return text, getattr(info, "language", None)

    async def _recognize_impl(
        self,
        buffer: AudioBuffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions,
    ) -> stt.SpeechEvent:
        lang = (language if is_given(language) else self._language) or None
        # WAV bytes at the source sample rate; faster-whisper resamples
        # to 16k internally.
        wav = rtc.combine_audio_frames(buffer).to_wav_bytes()
        try:
            model = await self.ensure_model()
            text, detected = await asyncio.to_thread(
                self._transcribe_sync, model, wav, lang
            )
        except Exception as e:
            # Surface as an APIConnectionError so the framework's retry
            # logic treats it like any provider hiccup.
            raise APIConnectionError(f"faster-whisper STT failed: {e}") from e

        return stt.SpeechEvent(
            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
            alternatives=[
                stt.SpeechData(text=text, language=detected or lang or "en")
            ],
        )


def build_stt() -> FasterWhisperSTT:
    """Construct the STT from env (VOICE_STT_MODEL / VOICE_STT_LANGUAGE)."""
    model = os.environ.get("VOICE_STT_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    # Pin English by default — whisper auto-detect on short clips is
    # unreliable (the desktop agent learned this live; JARVIS_LANG_AUTODETECT=0
    # is its shipped default). Set VOICE_STT_LANGUAGE=auto to re-enable.
    raw_lang = os.environ.get("VOICE_STT_LANGUAGE", "en").strip().lower()
    lang = None if raw_lang in ("auto", "") else raw_lang
    # .en models reject a language pin other than en; guard the mismatch.
    if model.endswith(".en"):
        lang = "en"
    inst = FasterWhisperSTT(model=model, language=lang)
    logger.info("[stt] armed: model=%s lang=%s", model, lang or "auto")
    return inst
