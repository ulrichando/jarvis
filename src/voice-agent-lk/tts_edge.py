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
import logging

import edge_tts
from livekit.agents import tts
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions
from livekit.agents.utils import shortuuid

logger = logging.getLogger("voice-agent.tts")

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
        on_speech=None,
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
        # Optional callback(text: str, words: list[{"t": word, "ms": offset})
        # invoked once per utterance with edge-tts word-boundary timings — lets
        # the phone light each word white as it's spoken (karaoke read-along).
        self._on_speech = on_speech

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

        # edge-tts 7.x emits SentenceBoundary (offset + duration + text, in
        # 100-ns ticks), not per-word timing. Interpolate word start-times across
        # each sentence's spoken span (weighted by character length) so the phone
        # can light each word white roughly as it's said. ms are relative to this
        # utterance's audio start; the phone stamps absolute targets on arrival.
        sentences: list[dict] = []
        sent = False

        def _emit_words() -> None:
            nonlocal sent
            if sent or self._tts._on_speech is None:  # type: ignore[attr-defined]
                return
            sent = True
            words: list[dict] = []
            for s in sentences:
                toks = s["text"].split()
                total = sum(len(t) for t in toks) or 1
                acc = 0
                for t in toks:
                    words.append({"t": t, "ms": s["off"] + int(acc / total * s["dur"])})
                    acc += len(t) + 1
            # Total spoken span of THIS utterance (last sentence end). Sent so the
            # phone can lay consecutive utterances on one timeline — synthesis is
            # faster than playback, so packets arrive batched but audio is
            # sequential.
            dur = max((s["off"] + s["dur"] for s in sentences), default=0)
            try:
                self._tts._on_speech(text, words, dur)  # type: ignore[attr-defined]
            except Exception as e:  # never let telemetry break synthesis
                logger.warning("[tts] on_speech callback failed: %s", e)

        try:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    output_emitter.push(chunk["data"])
                elif chunk["type"] == "SentenceBoundary":
                    sentences.append(
                        {
                            "off": int(chunk.get("offset", 0)) // 10_000,
                            "dur": int(chunk.get("duration", 0)) // 10_000,
                            "text": chunk.get("text", ""),
                        }
                    )
            _emit_words()  # all sentence timings known — publish the word list
            output_emitter.flush()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # APIConnectionError → the framework retries per conn_options.
            from livekit.agents._exceptions import APIConnectionError

            raise APIConnectionError(f"edge-tts request failed: {e}") from e
