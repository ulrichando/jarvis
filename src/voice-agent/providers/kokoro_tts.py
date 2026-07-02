"""Local Kokoro-82M TTS via an OpenAI-compatible /audio/speech endpoint.

Kokoro can't install into JARVIS's *pinned* voice-agent venv — it requires
``numpy==1.26.4`` (the venv runs 2.4.6, needed by livekit-agents +
faster-whisper/ctranslate2) and its ``misaki→spacy→blis`` G2P stack won't
compile on every CPU. So — exactly like Odysseus's ``endpoint:<id>`` TTS
provider — Kokoro runs as a SEPARATE OpenAI-compatible server (the standard
``kokoro-fastapi``, default ``:8880``) and this adapter is a thin HTTP
client. Zero pinned-venv impact, and it works identically whether the
server is on this box or the powerful GPU box.

The FINAL TTS rung when ``JARVIS_LOCAL_TTS_ENGINE=kokoro``. Non-streaming:
requests mp3 and lets the framework's AudioEmitter decode it (the same
well-trodden path ``providers/edge_tts.py`` uses). Part of the local
offline fallback stack — see ``docs/runbook/local-offline-fallback.md``.

Server (run on the GPU box):
    docker run -p 8880:8880 ghcr.io/remsky/kokoro-fastapi-gpu   # or -cpu
Then point JARVIS at it:
    JARVIS_LOCAL_TTS_ENABLED=1
    JARVIS_LOCAL_TTS_ENGINE=kokoro
    JARVIS_LOCAL_TTS_URL=http://GPU_BOX_IP:8880/v1
    JARVIS_LOCAL_TTS_VOICE=af_heart
"""
from __future__ import annotations

import asyncio
import logging
import os

from livekit.agents import tts
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions
from livekit.agents.utils import shortuuid

logger = logging.getLogger("jarvis.tts.local")

__all__ = ["KokoroEndpointTTS", "build_kokoro_tts"]

# Kokoro outputs 24 kHz mono; the AudioEmitter resamples to the room rate.
SAMPLE_RATE = 24000
NUM_CHANNELS = 1
DEFAULT_URL = "http://127.0.0.1:8880/v1"
DEFAULT_VOICE = "af_heart"


class KokoroEndpointTTS(tts.TTS):
    """Kokoro via an OpenAI-compatible /audio/speech server, as a livekit TTS."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_URL,
        voice: str = DEFAULT_VOICE,
        model: str = "kokoro",
        api_key: str = "",
        speed: float = 1.0,
        timeout: float = 60.0,
    ) -> None:
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=SAMPLE_RATE,
            num_channels=NUM_CHANNELS,
        )
        self._base_url = base_url.rstrip("/")
        self._voice = voice
        self._model = model
        self._api_key = api_key
        self._speed = speed
        self._timeout = timeout

    @property
    def model(self) -> str:
        return f"kokoro:{self._voice}"

    @property
    def provider(self) -> str:
        return "kokoro-local"

    def _current_voice(self) -> str:
        """Voice for THIS utterance — read fresh from ~/.jarvis/voice-tts-voice
        so a tray voice-pick hot-swaps with NO restart. Falls back to the
        construction-time voice when the file is absent/empty."""
        try:
            v = open(
                os.path.expanduser("~/.jarvis/voice-tts-voice"), encoding="utf-8"
            ).read().strip()
            if v:
                return v
        except Exception:
            pass
        return self._voice

    def _current_speed(self) -> float:
        """Speed for THIS utterance — read fresh from the voice-style
        store (~/.jarvis/tts-speed) so "Jarvis, speak slower" (the
        voice_style tool) or the tray Speech-rate pick applies on the
        very next sentence with NO restart. Falls back to the
        construction-time speed (JARVIS_LOCAL_TTS_SPEED) when unset."""
        try:
            from pipeline.voice_style import get_speed
            return get_speed(default=self._speed)
        except Exception:
            return self._speed

    def synthesize(
        self,
        text: str,
        *,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> tts.ChunkedStream:
        return _KokoroChunkedStream(tts=self, input_text=text, conn_options=conn_options)


class _KokoroChunkedStream(tts.ChunkedStream):
    """One synthesize() call → mp3 stream from the Kokoro server."""

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        k = self._tts
        assert isinstance(k, KokoroEndpointTTS)
        output_emitter.initialize(
            request_id=shortuuid(),
            sample_rate=SAMPLE_RATE,
            num_channels=NUM_CHANNELS,
            mime_type="audio/mpeg",  # mp3 → AudioEmitter decodes via av (same as EdgeTTS)
        )
        text = (self._input_text or "").strip()
        if not text:
            output_emitter.flush()
            return
        # Pronunciation lexicon (2026-07-02) — applied HERE on the payload
        # only, so `[word](/phonemes/)` markup never reaches the transcript
        # / chat history (committed markup would teach the LLM to mimic it).
        try:
            from pipeline.pronunciation import apply as _pron_apply
            text = _pron_apply(text, phonemes_ok=True)
        except Exception:
            pass

        import aiohttp

        url = k._base_url + "/audio/speech"
        headers = {"Content-Type": "application/json"}
        if k._api_key:
            headers["Authorization"] = f"Bearer {k._api_key}"
        payload = {
            "model": k._model,
            "input": text,
            "voice": k._current_voice(),  # read fresh → hot-swaps with no restart
            "response_format": "mp3",
            "speed": k._current_speed(),  # read fresh → "speak slower" hot-swaps too
        }
        try:
            timeout = aiohttp.ClientTimeout(total=k._timeout)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=payload, headers=headers) as resp:
                    resp.raise_for_status()
                    async for chunk in resp.content.iter_chunked(4096):
                        output_emitter.push(chunk)
            output_emitter.flush()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # Re-raise as livekit's APIError so the FallbackAdapter knows
            # to try the next TTS — though Kokoro, being last, means a
            # failure here surfaces as a normal TTS error.
            from livekit.agents._exceptions import APIConnectionError
            raise APIConnectionError(f"kokoro endpoint TTS failed: {e}") from e


def build_kokoro_tts() -> KokoroEndpointTTS | None:
    """Build the Kokoro endpoint TTS rung from env. The caller
    (``providers.piper_tts.build_local_tts``) has already verified
    ``JARVIS_LOCAL_TTS_ENABLED=1`` + ``JARVIS_LOCAL_TTS_ENGINE=kokoro``.
    """
    base_url = (
        os.environ.get("JARVIS_LOCAL_TTS_URL", DEFAULT_URL).strip().rstrip("/")
        or DEFAULT_URL
    )
    voice = os.environ.get("JARVIS_LOCAL_TTS_VOICE", "").strip() or DEFAULT_VOICE
    model = os.environ.get("JARVIS_LOCAL_TTS_MODEL", "").strip() or "kokoro"
    api_key = os.environ.get("JARVIS_LOCAL_TTS_API_KEY", "").strip()
    try:
        speed = float(os.environ.get("JARVIS_LOCAL_TTS_SPEED", "1.0"))
    except (TypeError, ValueError):
        speed = 1.0
    try:
        inst = KokoroEndpointTTS(
            base_url=base_url, voice=voice, model=model, api_key=api_key, speed=speed
        )
        logger.info("[tts.local] Kokoro endpoint TTS rung armed: %s voice=%s", base_url, voice)
        return inst
    except Exception as e:
        logger.warning("[tts.local] Kokoro construction failed: %s", e)
        return None
