"""Microsoft Edge-TTS adapter for livekit-agents (headless VPS agent).

Adapted from the desktop agent's providers/edge_tts.py, minus the
desktop-only voice-style/pronunciation hooks. Edge TTS is the neural
endpoint the Edge browser uses — no auth, no quota in practice, and the
same voice (en-GB-RyanNeural) the JARVIS Android app already ships.

Sec-MS-GEC note: Microsoft rotates the client-version token that gates
this endpoint. The edge-tts package computes Sec-MS-GEC itself (DRM
clock-skew handling included, it self-corrects on 403 once), so unlike
the Android app there is no hardcoded version to bump — keeping the
`edge-tts` pip package current is the fix if Microsoft breaks it.
Failures are re-raised as APIConnectionError so the framework's
connection-retry policy (3 attempts by default) covers transient 403s.

Output: MP3 (audio/mpeg) — the AudioEmitter decodes via av, same path
the openai TTS plugin uses for its mp3 mode.
"""
from __future__ import annotations

import asyncio

import edge_tts
from livekit.agents import tts
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions
from livekit.agents.utils import shortuuid

__all__ = ["EdgeTTS", "SAMPLE_RATE", "NUM_CHANNELS", "DEFAULT_VOICE"]

# Edge-TTS streams MP3 at 24 kHz mono; the framework resamples downstream.
SAMPLE_RATE = 24000
NUM_CHANNELS = 1
DEFAULT_VOICE = "en-GB-RyanNeural"  # matches the Android app's voice


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
            tts=self, input_text=text, conn_options=conn_options
        )


class _EdgeTTSChunkedStream(tts.ChunkedStream):
    """Single synthesize() call → MP3 byte stream."""

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        output_emitter.initialize(
            request_id=shortuuid(),
            sample_rate=SAMPLE_RATE,
            num_channels=NUM_CHANNELS,
            mime_type="audio/mpeg",
        )

        # Whitespace-only input makes the endpoint return "No audio was
        # received", which would surface as a hard error. Skip silently.
        text = (self._input_text or "").strip()
        if not text:
            output_emitter.flush()
            return

        edge_tts_obj = self._tts
        assert isinstance(edge_tts_obj, EdgeTTS)
        communicate = edge_tts.Communicate(
            text,
            edge_tts_obj._voice,
            rate=edge_tts_obj._rate,
            volume=edge_tts_obj._volume,
            pitch=edge_tts_obj._pitch,
        )

        try:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    output_emitter.push(chunk["data"])
            output_emitter.flush()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # APIConnectionError → the framework retries per conn_options.
            from livekit.agents._exceptions import APIConnectionError

            raise APIConnectionError(f"edge-tts request failed: {e}") from e
