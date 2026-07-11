"""Local faster-whisper STT — offline last-resort speech-to-text.

A custom livekit :class:`stt.STT` that runs OpenAI Whisper locally via
faster-whisper (ctranslate2). It is the FINAL rung of JARVIS's STT
FallbackAdapter chain (Deepgram → THIS) — and the sole/primary rung
under the live ``JARVIS_STT_LOCAL_ONLY=1`` config — activated only
when ``JARVIS_LOCAL_STT_ENABLED=1``.

Non-streaming (finals only) — the chain's ``StreamAdapter`` + Silero VAD
wraps it for streaming compatibility, as with any finals-only Whisper STT.
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
import re

from livekit import rtc
from livekit.agents import APIConnectionError, APIConnectOptions, stt
from livekit.agents.types import NOT_GIVEN, NotGivenOr
from livekit.agents.utils import AudioBuffer, is_given

logger = logging.getLogger("jarvis.stt.local")


# Transient GPU failure markers. ctranslate2 raises RuntimeError shapes like
# "CUDA failed with error out of memory" or "parallel_for failed:
# cudaErrorInvalidDevice: invalid device ordinal" when whisper shares the
# 6 GB card with the local LLM. Live incidents 2026-07-10/11: the FIRST
# error in every failure cycle is a genuine CUDA **OOM** (ollama's
# llama-server holds ~3 GB of 6 GB and the whisper encode's activation
# spike exceeds the remainder); the invalid-device error on the next
# attempt is the OOM's downstream corruption of the CUDA context — NOT an
# independent compute/launch-contention failure as earlier comments here
# claimed. Without in-rung retries a single blip cascades: the framework's
# outer recognize() retries land in the same window, exhaust, mark the
# session unrecoverable, and the watchdog tears the whole AgentSession down.
_TRANSIENT_GPU_ERR_RE = re.compile(
    r"parallel_for failed|\bcuda|cublas|cudnn", re.I
)

# OOM-specific subset of the above: when GPU retries exhaust on one of
# these, retrying on the GPU can't cure it (the VRAM is held by the local
# LLM) — degrade THIS clip to a CPU transcription instead of killing the
# session (see _recognize_impl).
_OOM_ERR_RE = re.compile(
    r"out of memory|cudaErrorMemoryAllocation|CUBLAS_STATUS_ALLOC_FAILED", re.I
)


def _gpu_retries() -> int:
    """Bounded in-rung retry count for transient GPU errors (default 2 —
    i.e. up to 3 attempts per recognize; the framework's outer retry loop
    multiplies this, so a genuinely dead GPU still surfaces in seconds,
    not forever). Env-tunable via JARVIS_LOCAL_STT_GPU_RETRIES."""
    try:
        return max(0, int(os.environ.get("JARVIS_LOCAL_STT_GPU_RETRIES", "2")))
    except ValueError:
        return 2


class FasterWhisperSTT(stt.STT):
    """Local Whisper (faster-whisper) as a non-streaming livekit STT."""

    def __init__(
        self,
        *,
        model: str = "large-v3-turbo",
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
        self._cpu_model = None  # lazy CPU model for the GPU-OOM fallback
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

    def _ensure_cpu_model(self):
        """Lazily-built, cached CPU model for the GPU-OOM per-clip fallback.

        Called from a worker thread (sync on purpose). ponytail: once the
        first OOM fallback fires this keeps a ~1.6 GB model resident in RAM
        for the process lifetime — acceptable as the rare last resort, the
        box has RAM to spare.
        """
        if self._cpu_model is None:
            from faster_whisper import WhisperModel
            logger.info(
                "[stt.local] loading CPU fallback model=%s (int8)",
                self._model_size,
            )
            self._cpu_model = WhisperModel(
                self._model_size, device="cpu", compute_type="int8"
            )
        return self._cpu_model

    def _transcribe_sync(self, model, wav: bytes, lang: str | None):
        segments, info = model.transcribe(
            io.BytesIO(wav),
            language=lang,
            beam_size=1,        # fast; this is a last-resort rung
            vad_filter=False,   # the chain's Silero VAD already gated this audio
        )
        text = "".join(seg.text for seg in segments).strip()
        return text, getattr(info, "language", None)

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
        # Transient-GPU retry loop (2026-07-10): on a transient CUDA error
        # (see _TRANSIENT_GPU_ERR_RE) retry the SAME audio a bounded number of
        # times with a small backoff instead of surfacing immediately — the
        # backoff lets the concurrent local-LLM burst clear the card. Before
        # the FINAL attempt the model is dropped + lazily reloaded, rebuilding
        # the ctranslate2 CUDA context (an OOM — or a rarer non-OOM blip —
        # can wedge the existing one, in which case in-context retries never
        # recover). When retries exhaust on a genuine OOM (2026-07-11: the
        # local LLM holds the VRAM, so GPU retries re-OOM forever) this clip
        # degrades to a one-shot CPU transcription instead of dying; the next
        # clip retries the GPU as normal. Non-GPU errors and exhausted
        # non-OOM retries surface exactly as before: an APIConnectionError
        # the chain/framework can cascade on.
        retries = _gpu_retries()
        attempt = 0
        while True:
            try:
                model = await self._ensure_model()
                text, detected = await asyncio.to_thread(
                    self._transcribe_sync, model, wav, lang
                )
                break
            except Exception as e:  # surface as a chain-cascadable error
                blob = f"{type(e).__name__} {e}"
                if attempt >= retries or not _TRANSIENT_GPU_ERR_RE.search(blob):
                    if _OOM_ERR_RE.search(blob) and self._device != "cpu":
                        # GPU OOM persists — degrade THIS clip only. Keep
                        # self._device untouched so the next clip retries
                        # the GPU (VRAM may have freed).
                        logger.warning(
                            "[stt.local] GPU OOM persists — transcribing this "
                            "clip on CPU (slow) so the utterance isn't dropped"
                        )
                        try:
                            def _cpu_transcribe():
                                return self._transcribe_sync(
                                    self._ensure_cpu_model(), wav, lang
                                )
                            text, detected = await asyncio.to_thread(_cpu_transcribe)
                            break
                        except Exception as cpu_e:
                            raise APIConnectionError(
                                "faster-whisper local STT failed (GPU OOM; "
                                f"CPU fallback also failed): {cpu_e}"
                            ) from cpu_e
                    raise APIConnectionError(
                        f"faster-whisper local STT failed: {e}"
                    ) from e
                attempt += 1
                delay = 0.4 * (2 ** (attempt - 1))
                if attempt >= retries:
                    # Last chance: rebuild the model → fresh CUDA context.
                    logger.warning(
                        "[stt.local] transient GPU error persists — dropping the "
                        "model for a fresh CUDA context before the final retry"
                    )
                    self._model = None
                logger.warning(
                    "[stt.local] transient GPU error (attempt %d/%d), retrying "
                    "in %.1fs: %s",
                    attempt, retries, delay, str(e)[:200],
                )
                await asyncio.sleep(delay)

        return stt.SpeechEvent(
            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
            alternatives=[stt.SpeechData(text=text, language=detected or lang or "en")],
        )


def build_local_stt() -> FasterWhisperSTT | None:
    """Construct the local STT rung from env, or None when disabled.

    Gated on ``JARVIS_LOCAL_STT_ENABLED=1``. Defaults: large-v3-turbo on
    CPU/int8 (robust, no GPU/cuDNN dependency, fine for a last-resort
    rung; turbo — not the bigger large-v3 — is the documented live model
    and its smaller VRAM footprint leaves headroom next to the local LLM
    on the 6 GB card). ``device=auto`` is coerced to ``cpu`` to avoid VRAM
    contention with the local LLM + cuDNN requirements; set
    ``JARVIS_LOCAL_STT_DEVICE=cuda`` explicitly on a box set up for it.
    """
    if os.environ.get("JARVIS_LOCAL_STT_ENABLED", "0") != "1":
        return None
    model = (
        os.environ.get("JARVIS_LOCAL_STT_MODEL", "large-v3-turbo").strip()
        or "large-v3-turbo"
    )
    device = os.environ.get("JARVIS_LOCAL_STT_DEVICE", "cpu").strip() or "cpu"
    compute = os.environ.get("JARVIS_LOCAL_STT_COMPUTE", "int8").strip() or "int8"
    if device == "auto":
        device = "cpu"
    # Language pin — mirrors stt.py::_stt_language (same env knob, same
    # default; duplicated 2 lines rather than imported, to avoid a
    # providers.stt <-> faster_whisper_stt cycle — keep them in sync).
    # JARVIS_LANG_AUTODETECT falsy -> pin 'en'; default/truthy -> auto.
    # Live 2026-07-01 (multilingual room): auto-detect transcribed the
    # user's ENGLISH as Portuguese ("Jarvis, contem de um para dez,
    # lentamente.", detect prob 0.21) and the supervisor refused it as
    # non-English input; .env now ships JARVIS_LANG_AUTODETECT=0.
    raw = os.environ.get("JARVIS_LANG_AUTODETECT", "1").strip().lower()
    lang = "en" if raw in ("0", "false", "off", "no", "") else None
    try:
        inst = FasterWhisperSTT(
            model=model, device=device, compute_type=compute, language=lang,
        )
        logger.info(
            "[stt.local] faster-whisper rung armed: model=%s device=%s compute=%s lang=%s",
            model, device, compute, lang or "auto",
        )
        return inst
    except Exception as e:
        logger.warning("[stt.local] faster-whisper construction failed: %s", e)
        return None
