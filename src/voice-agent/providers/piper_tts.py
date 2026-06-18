"""Local Piper TTS — offline last-resort text-to-speech.

A custom livekit :class:`tts.TTS` that runs Piper (neural TTS on
onnxruntime) fully locally. The FINAL rung of JARVIS's TTS
FallbackAdapter chain (Groq Orpheus → Edge-TTS → THIS), activated only
when ``JARVIS_LOCAL_TTS_ENABLED=1``. Edge-TTS still needs the network;
Piper is the truly-offline backstop so JARVIS keeps a voice with no
internet at all.

Non-streaming: ``synthesize()`` runs Piper over the full text and pushes
raw int16 PCM to the AudioEmitter (``mime_type="audio/pcm"``). The voice
model + sample rate come from the model's own config (e.g.
en_US-lessac-medium → 22050 Hz). Voice model path:
``JARVIS_LOCAL_TTS_MODEL_PATH`` (a ``.onnx``; its sibling ``.onnx.json``
is the config). Piper's phonemization uses the espeak-ng data bundled in
the piper-tts wheel — no system espeak dependency.

Part of the local offline fallback stack — see ``pipeline/config.py`` and
the 2026-06-15 local-LLM design
(~/.claude/plans/we-need-to-find-polymorphic-allen.md). Modeled on
``providers/edge_tts.py``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

from livekit.agents import tts
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions
from livekit.agents.utils import shortuuid

logger = logging.getLogger("jarvis.tts.local")

__all__ = ["PiperTTS", "build_local_tts"]

NUM_CHANNELS = 1
DEFAULT_MODEL_PATH = Path.home() / ".jarvis" / "models" / "piper" / "en_US-lessac-medium.onnx"


class PiperTTS(tts.TTS):
    """Local Piper neural TTS as a livekit ``tts.TTS`` implementation."""

    def __init__(self, *, model_path: str, sample_rate: int | None = None) -> None:
        sr = sample_rate or self._read_config_sample_rate(model_path) or 22050
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=sr,
            num_channels=NUM_CHANNELS,
        )
        self._model_path = str(model_path)
        self._sample_rate = sr
        self._voice = None  # lazy-loaded PiperVoice
        self._load_lock = asyncio.Lock()

    @staticmethod
    def _read_config_sample_rate(model_path: str) -> int | None:
        """Pull the voice's native sample rate from its <model>.onnx.json."""
        try:
            cfg = Path(str(model_path) + ".json")
            if not cfg.exists():
                cfg = Path(model_path).with_suffix(".onnx.json")
            data = json.loads(cfg.read_text())
            return int(data.get("audio", {}).get("sample_rate") or 0) or None
        except Exception:
            return None

    @property
    def model(self) -> str:
        return f"piper:{Path(self._model_path).stem}"

    @property
    def provider(self) -> str:
        return "piper-local"

    async def _ensure_voice(self):
        if self._voice is not None:
            return self._voice
        async with self._load_lock:
            if self._voice is None:
                def _load():
                    from piper import PiperVoice
                    return PiperVoice.load(self._model_path)
                logger.info("[tts.local] loading Piper voice: %s", self._model_path)
                self._voice = await asyncio.to_thread(_load)
        return self._voice

    def synthesize(
        self,
        text: str,
        *,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> tts.ChunkedStream:
        return _PiperChunkedStream(tts=self, input_text=text, conn_options=conn_options)


class _PiperChunkedStream(tts.ChunkedStream):
    """Single synthesize() call → raw int16 PCM pushed to the emitter."""

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        piper = self._tts
        assert isinstance(piper, PiperTTS)
        output_emitter.initialize(
            request_id=shortuuid(),
            sample_rate=piper._sample_rate,
            num_channels=NUM_CHANNELS,
            mime_type="audio/pcm",
        )
        text = (self._input_text or "").strip()
        if not text:
            output_emitter.flush()
            return
        try:
            voice = await piper._ensure_voice()

            def _synth() -> bytes:
                buf = bytearray()
                for chunk in voice.synthesize(text):
                    buf += chunk.audio_int16_bytes
                return bytes(buf)

            pcm = await asyncio.to_thread(_synth)
            if pcm:
                output_emitter.push(pcm)
            output_emitter.flush()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # Re-raise as livekit's APIError so the FallbackAdapter knows
            # to try the next TTS — though Piper, being last, means a
            # failure here just surfaces as a normal TTS error.
            from livekit.agents._exceptions import APIConnectionError
            raise APIConnectionError(f"piper local TTS failed: {e}") from e


def build_local_tts() -> "tts.TTS | None":
    """Construct the local TTS rung from env, or None when disabled.

    Gated on ``JARVIS_LOCAL_TTS_ENABLED=1``. Dispatches on
    ``JARVIS_LOCAL_TTS_ENGINE``:
      - ``piper`` (default) — in-process Piper from ``JARVIS_LOCAL_TTS_MODEL_PATH``
        (a ``.onnx``; defaults to ~/.jarvis/models/piper/en_US-lessac-medium.onnx).
      - ``kokoro`` — Kokoro-82M via a separate OpenAI-compat /audio/speech
        server (it can't share the pinned venv); see providers/kokoro_tts.py.
    """
    if os.environ.get("JARVIS_LOCAL_TTS_ENABLED", "0") != "1":
        return None
    engine = os.environ.get("JARVIS_LOCAL_TTS_ENGINE", "piper").strip().lower()
    if engine == "kokoro":
        from providers.kokoro_tts import build_kokoro_tts  # lazy — keeps aiohttp/import cost off the piper path
        return build_kokoro_tts()
    if engine != "piper":
        logger.warning("[tts.local] engine %r not supported; use 'piper' or 'kokoro'", engine)
        return None
    model_path = os.environ.get("JARVIS_LOCAL_TTS_MODEL_PATH", "").strip() or str(DEFAULT_MODEL_PATH)
    if not Path(model_path).exists():
        logger.warning("[tts.local] Piper model not found at %s; rung disabled", model_path)
        return None
    try:
        inst = PiperTTS(model_path=model_path)
        logger.info("[tts.local] Piper TTS rung armed: %s", model_path)
        return inst
    except Exception as e:
        logger.warning("[tts.local] Piper construction failed: %s", e)
        return None
