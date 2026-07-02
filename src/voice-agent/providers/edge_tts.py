"""
Microsoft Edge-TTS adapter for livekit-agents.

Why this exists: Groq Orpheus TTS (our previous primary) has had
intermittent service-side outages. Microsoft's Edge browser uses a
neural-TTS endpoint reachable via the open-source `edge-tts` PyPI
package — same voices Azure Cognitive Services serves, no auth, no
quota in practice. Better availability than any single paid provider.

Wraps `edge_tts.Communicate` in livekit's `tts.TTS` / `ChunkedStream`
interface so it drops into AgentSession the same way `groq.TTS()` does.

Output format: edge-tts streams MP3 (audio/mpeg). The agent's
AudioEmitter decodes it transparently — same path the openai TTS
plugin uses for its mp3 mode.
"""
from __future__ import annotations

import asyncio
from typing import Any

import edge_tts
from livekit.agents import tts
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions
from livekit.agents.utils import shortuuid

__all__ = ["EdgeTTS", "SAMPLE_RATE", "NUM_CHANNELS", "DEFAULT_VOICE"]


# Edge-TTS streams MP3 at 24 kHz mono. The AudioEmitter resamples
# downstream to whatever the room expects.
SAMPLE_RATE = 24000
NUM_CHANNELS = 1
DEFAULT_VOICE = "en-US-GuyNeural"


class EdgeTTS(tts.TTS):
    """Microsoft Edge-TTS as a livekit `tts.TTS` implementation."""

    def __init__(
        self,
        *,
        voice: str = DEFAULT_VOICE,
        rate: str = "+0%",
        volume: str = "+0%",
        pitch: str = "+0Hz",
    ) -> None:
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=SAMPLE_RATE,
            num_channels=NUM_CHANNELS,
        )
        self._voice = voice
        self._rate = rate
        self._volume = volume
        self._pitch = pitch

    @property
    def model(self) -> str:
        # Surface in the metrics span / "what model are you" answer.
        return f"edge-tts:{self._voice}"

    @property
    def provider(self) -> str:
        return "microsoft-edge-tts"

    def synthesize(
        self,
        text: str,
        *,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> tts.ChunkedStream:
        return _EdgeTTSChunkedStream(
            tts=self,
            input_text=text,
            conn_options=conn_options,
        )


class _EdgeTTSChunkedStream(tts.ChunkedStream):
    """Single synthesize() call → MP3 byte stream."""

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        request_id = shortuuid()
        # The framework-side AudioEmitter handles MP3 → PCM decode
        # via av (the bundled FFmpeg wrapper). Same path openai's
        # response_format=mp3 uses; well-trodden.
        output_emitter.initialize(
            request_id=request_id,
            sample_rate=SAMPLE_RATE,
            num_channels=NUM_CHANNELS,
            mime_type="audio/mpeg",
        )

        # Whitespace-only input would make Microsoft's TTS endpoint
        # return "No audio was received" — which propagates as a hard
        # failure through the FallbackAdapter and triggers the user's
        # error notification. Most common cause: the agent's
        # strip_function_call_leakage filter stripped a turn that was
        # nothing but a leaked function-call markup. Skip silently.
        text = (self._input_text or "").strip()
        if not text:
            output_emitter.flush()
            return

        edge_tts_obj = self._tts  # for type narrowing
        assert isinstance(edge_tts_obj, EdgeTTS)
        # rate/pitch read fresh from the voice-style store per utterance
        # ("Jarvis, speak slower" hot-swaps with no restart — mirrors the
        # Kokoro provider). Construction-time values remain the fallback
        # when no override file exists.
        try:
            from pipeline.voice_style import edge_rate_string, edge_pitch_string
            rate = edge_rate_string(edge_tts_obj._rate)
            pitch = edge_pitch_string(edge_tts_obj._pitch)
        except Exception:
            rate, pitch = edge_tts_obj._rate, edge_tts_obj._pitch
        # Pronunciation lexicon — respellings only (Edge has no phoneme
        # syntax; a Misaki override here would be read aloud as IPA).
        try:
            from pipeline.pronunciation import apply as _pron_apply
            text = _pron_apply(text, phonemes_ok=False)
        except Exception:
            pass
        communicate = edge_tts.Communicate(
            text,
            edge_tts_obj._voice,
            rate=rate,
            volume=edge_tts_obj._volume,
            pitch=pitch,
        )

        try:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    output_emitter.push(chunk["data"])
            output_emitter.flush()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # Re-raise as livekit's APIError so the framework's
            # FallbackAdapter knows to try the next TTS in the chain.
            from livekit.agents._exceptions import APIConnectionError
            raise APIConnectionError(
                f"edge-tts request failed: {e}",
            ) from e
