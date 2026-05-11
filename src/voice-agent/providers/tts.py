"""Groq Orpheus TTS adapters + per-route dispatcher build.

Hoisted from `jarvis_agent.py` 2026-05-10 (Step 6 of the 10/10
refactor). Three things land here together because they're tightly
coupled:

  - `LoggingGroqChunkedStream` — subclass of the upstream groq TTS
    stream that (a) short-circuits punctuation-only inputs to silent
    WAV (Groq rejects letterless input with 400), (b) routes the
    upstream call through `TTS_BREAKER` for fail-fast on cooldown,
    (c) logs Groq's response body on non-2xx (the upstream plugin
    constructs APIStatusError with body=None on non-2xx so without
    this shim we only see "Bad Request" with no detail), and (d)
    records a position-table entry for barge-in truncation.
  - `LoggingGroqTTS` — `groq.TTS` that returns
    `LoggingGroqChunkedStream` from `synthesize()`.
  - `build_tts_chain` / `build_dispatching_tts` — assemble the
    FallbackAdapter([groq, edge]) chains the AgentSession uses.

Telemetry callbacks live in jarvis_agent:
  - `_record_synthesis(session, input_chars, audio_bytes)` writes a
    row to the per-turn barge-in position table.
  - `_active_session_for_telemetry[0]` holds the live AgentSession so
    `_record_synthesis` can find it.

The chunked-stream class imports both lazily inside `_run` so this
module doesn't pull jarvis_agent into scope at import time.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re

import aiohttp as _aiohttp
from livekit.agents import (
    APIConnectionError,
    APIError,
    APIStatusError,
    APITimeoutError,
    tts,
    utils as lk_utils,
)
from livekit.plugins import groq
from livekit.plugins.groq.tts import ChunkedStream as _GroqChunkedStream
from providers import edge_tts as edge_tts_plugin

from pipeline.dispatching_tts import DispatchingTTS
from pipeline.settings import read_unified_setting
from resilience import TTS_BREAKER
from resilience.circuit_breaker import CircuitOpenError


logger = logging.getLogger("jarvis-agent")


# ── Groq TTS error-body logging shim ─────────────────────────────────

class LoggingGroqChunkedStream(_GroqChunkedStream):
    async def _run(self, output_emitter) -> None:
        # Lazy import — these telemetry callbacks live in jarvis_agent.
        # Module load order: jarvis_agent imports providers.tts; this
        # function runs only after both are loaded, so the import
        # resolves cleanly without a circular-import boot trap.
        from jarvis_agent import _record_synthesis, _active_session_for_telemetry

        # Track audio bytes emitted this synthesize() call so we can
        # append a position-table entry for barge-in truncation.
        # Wrapped in a 1-element list so the nested _do_real_run can
        # mutate it without `nonlocal` boilerplate.
        # Spec: docs/superpowers/specs/2026-05-07-barge-in-truncation-design.md
        nonlocal_audio_bytes = [0]
        # Groq Orpheus rejects synth requests where the input contains
        # no letters or digits — returns 400 "Input must contain at
        # least one letter or digit" (verified by the response-body
        # logger on 2026-04-26). LLMs occasionally emit punctuation-
        # only chunks ("...", "—", "  ", a single emoji); we'd burn a
        # round-trip + retry budget on each one, then fall through to
        # EdgeTTS late. Short-circuit here: empty audio is the correct
        # output for letterless input anyway.
        if not re.search(r"[A-Za-z0-9]", self._input_text or ""):
            # Push a tiny silent WAV so the FallbackAdapter sees a
            # successful (but inaudible) stream and does NOT cascade
            # to EdgeTTS. An empty flush() (no frames pushed) triggers
            # "no audio frames were pushed" warnings and a retry loop
            # that spams errors for hours — verified 2026-04-27.
            import struct as _struct
            _n = 480  # 10ms of silence at 48 kHz mono 16-bit
            _wav = (
                b"RIFF" + _struct.pack("<I", 36 + _n * 2) + b"WAVE"
                + b"fmt " + _struct.pack("<IHHIIHH", 16, 1, 1, 48000, 96000, 2, 16)
                + b"data" + _struct.pack("<I", _n * 2)
                + b"\x00" * (_n * 2)
            )
            output_emitter.initialize(
                request_id=lk_utils.shortuuid(),
                sample_rate=48000,
                num_channels=1,
                mime_type="audio/wav",
            )
            output_emitter.push(_wav)
            nonlocal_audio_bytes[0] += len(_wav)
            output_emitter.flush()
            # Record this (silent) call in the position table so subsequent
            # synthesize() calls in the same turn see correct running totals.
            _record_synthesis(
                _active_session_for_telemetry[0],
                len(self._input_text or ""),
                nonlocal_audio_bytes[0],
            )
            return
        # Breaker-gated upstream call. TTS_BREAKER fails fast when
        # Groq's TTS endpoint is in cooldown so FallbackAdapter
        # cascades to EdgeTTS within ms instead of waiting ~30s for
        # the aiohttp socket to time out. Existing exception handlers
        # for HTTP / status / generic errors stay inside _do_real_run
        # so behaviour is unchanged when the breaker is closed.
        async def _do_real_run():
            api_url = f"{self._opts.base_url}/audio/speech"
            payload = {
                "model": self._opts.model,
                "voice": self._opts.voice,
                "input": self._input_text,
                "response_format": "wav",
            }
            try:
                async with self._tts._ensure_session().post(
                    api_url,
                    headers={
                        "Authorization": f"Bearer {self._opts.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=_aiohttp.ClientTimeout(
                        total=30, sock_connect=self._conn_options.timeout
                    ),
                ) as resp:
                    if resp.status >= 400:
                        body = await resp.text()
                        logger.error(
                            "Groq TTS %d (model=%s voice=%s): %s",
                            resp.status,
                            payload["model"],
                            payload["voice"],
                            body[:600].replace("\n", " "),
                        )
                        raise APIStatusError(
                            message=f"Groq TTS {resp.status}: {body[:200]}",
                            status_code=resp.status,
                            request_id=None,
                            body=body,
                        )
                    if not resp.content_type.startswith("audio"):
                        content = await resp.text()
                        logger.error(
                            "Groq TTS returned non-audio (%s): %s",
                            resp.content_type,
                            content[:300],
                        )
                        raise APIError(
                            message="Groq returned non-audio data", body=content
                        )
                    output_emitter.initialize(
                        request_id=lk_utils.shortuuid(),
                        sample_rate=48000,
                        num_channels=1,
                        mime_type="audio/wav",
                    )
                    async for data, _ in resp.content.iter_chunks():
                        output_emitter.push(data)
                        nonlocal_audio_bytes[0] += len(data)
                    output_emitter.flush()
            except asyncio.TimeoutError:
                raise APITimeoutError() from None
            except APIError:
                raise
            except _aiohttp.ClientResponseError as e:
                raise APIStatusError(
                    message=e.message, status_code=e.status, request_id=None, body=None
                ) from None
            except Exception as e:
                raise APIConnectionError() from e

        try:
            await TTS_BREAKER.call(_do_real_run)
        except CircuitOpenError as e:
            raise APIConnectionError() from e
        except asyncio.TimeoutError:
            raise APITimeoutError() from None
        # Record this synthesize() call's position-table entry. Runs ONLY
        # on success path — on breaker exception above, the audio wasn't
        # actually played so we don't append.
        _record_synthesis(
            _active_session_for_telemetry[0],
            len(self._input_text or ""),
            nonlocal_audio_bytes[0],
        )

    @staticmethod
    async def _call_with_breaker_for_test():
        """Test seam — exercises only the breaker-open path with a
        no-op coroutine. Cheap to invoke and proves the breaker
        conversion (`CircuitOpenError` → `APIConnectionError`,
        `asyncio.TimeoutError` → `APITimeoutError`) works in isolation.

        Limitation: this seam does NOT exercise the full caller
        contract (e.g. `async with stream: async for chunk in stream:`
        used by livekit-agents). Tests that need to verify the wrapper
        honours protocol methods must construct the wrapper class
        directly and drive it through async with + async for — see
        test_breaker_llm_open_raises_apiconnection_error for the
        pattern."""
        async def _no_op():
            return None
        try:
            return await TTS_BREAKER.call(_no_op)
        except CircuitOpenError as e:
            raise APIConnectionError() from e
        except asyncio.TimeoutError:
            raise APITimeoutError() from None


class LoggingGroqTTS(groq.TTS):
    """`groq.TTS` that logs Groq's response body on non-2xx."""

    def synthesize(self, text, *, conn_options=None):
        from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS

        return LoggingGroqChunkedStream(
            tts=self,
            input_text=text,
            conn_options=conn_options or DEFAULT_API_CONNECT_OPTIONS,
        )


# ── TTS chain + dispatcher build ─────────────────────────────────────

def build_tts_chain(tts_provider_file) -> list:
    """Build the ordered TTS list for FallbackAdapter.

    Priority (first wins):
      1. ~/.jarvis/tts-provider file — written by the tray's Voice submenu
      2. Default: Groq Orpheus (voice from JARVIS_TTS_VOICE env)
    Always appended last: Edge-TTS (no auth, always available).

    ElevenLabs was removed 2026-05-01 after the live key 401-d and
    the FallbackAdapter chain failed to recover (both EL and edge_tts
    returned 0 frames during the same window, leaving JARVIS silent
    and poisoning the chat_ctx with a half-completed assistant turn).

    `tts_provider_file` is the Path to the legacy flat file written
    by the tray, passed in so this module doesn't reach back into
    jarvis_agent for it.
    """
    groq_voice = os.getenv("JARVIS_TTS_VOICE", "troy")
    edge_voice = os.getenv("JARVIS_EDGE_VOICE", "en-US-GuyNeural")

    primary = None
    spec = read_unified_setting("tts-provider", tts_provider_file)
    if spec and ":" in spec:
        provider, voice = spec.split(":", 1)
        provider = provider.strip()
        voice    = voice.strip()
        if provider == "groq":
            primary = LoggingGroqTTS(
                model="canopylabs/orpheus-v1-english", voice=voice,
            )
            logger.info(f"[tts] Groq Orpheus voice={voice} [tray selection]")
        else:
            logger.warning(
                f"[tts] unknown / removed provider {provider!r}; "
                f"falling back to Groq Orpheus default"
            )

    if primary is None:
        primary = LoggingGroqTTS(
            model="canopylabs/orpheus-v1-english", voice=groq_voice,
        )
        logger.info(f"[tts] Groq Orpheus voice={groq_voice} [default]")

    return [primary, edge_tts_plugin.EdgeTTS(voice=edge_voice)]


def build_dispatching_tts() -> DispatchingTTS:
    """Per-route inner Groq Orpheus TTS instances with different voices.

    Voices are env-overridable via
    JARVIS_VOICE_{BANTER,TASK,REASONING,EMOTIONAL}.
    All four routes use Groq Orpheus (fast, cheap, reliable).
    ElevenLabs was removed 2026-05-01 after the live key 401-d and
    the safety-net edge_tts fallback ALSO returned 0 frames in the
    same window — the StreamAdapter+EL+edge cascade had a real
    failure mode that left JARVIS silent mid-turn. Orpheus has its
    own intermittent silent-frame bug, but
    `FallbackAdapter([orpheus, edge_tts])` handles it cleanly.
    """
    # Orpheus voices for all four routes. Per-route picks come from env.
    orph = {
        "BANTER":    os.environ.get("JARVIS_VOICE_BANTER", "austin"),
        "TASK":      os.environ.get("JARVIS_VOICE_TASK",   "troy"),
        "REASONING": os.environ.get("JARVIS_VOICE_REASONING", "troy"),
        "EMOTIONAL": os.environ.get("JARVIS_VOICE_EMOTIONAL", "daniel"),
    }

    # Single shared edge_tts instance used as the fallback inside every
    # route's FallbackAdapter. Microsoft's Edge TTS is auth-free, has no
    # practical quota, and survives Groq Orpheus's intermittent "no
    # audio frames pushed" failures (which were leaving JARVIS silent
    # mid-conversation as of 2026-04-30). Voice id is the SAME en-US
    # neural voice the legacy chain uses.
    edge_voice = os.environ.get("JARVIS_EDGE_VOICE", "en-US-ChristopherNeural")
    try:
        _edge_fallback = edge_tts_plugin.EdgeTTS(voice=edge_voice)
        _edge_fallback.voice_id = f"edge:{edge_voice[:10]}…"
    except Exception as e:
        logger.warning(f"[dispatch] edge_tts construction failed ({e}); routes will have no fallback")
        _edge_fallback = None

    inners: dict[str, object] = {}
    fallback = None

    def _wrap_with_edge_fallback(primary):
        """Wrap a per-route TTS in a FallbackAdapter so when the primary
        returns no audio frames (Orpheus or ElevenLabs intermittent),
        edge_tts takes over. Preserves the .voice_id attribute the
        DispatchingTTS exposes for telemetry."""
        if _edge_fallback is None:
            return primary
        try:
            wrapped = tts.FallbackAdapter([primary, _edge_fallback])
            wrapped.voice_id = getattr(primary, "voice_id", "?")
            return wrapped
        except Exception as e:
            logger.warning(f"[dispatch] FallbackAdapter wrap failed ({e}); using primary alone")
            return primary

    for route in ("BANTER", "TASK", "REASONING", "EMOTIONAL"):
        # Orpheus path. Orpheus capability is streaming=False (whole-reply
        # synthesis), so wrap in StreamAdapter to make the framework
        # synthesize sentence-by-sentence — first sentence's audio plays
        # while later sentences are still generating. text_pacing=True
        # paces playback to match the LLM's text rate, hiding any TTS
        # synthesis-side jitter. Cuts TTFW from full-synth latency to
        # first-sentence latency.
        vid = orph[route]
        try:
            raw = LoggingGroqTTS(model="canopylabs/orpheus-v1-english", voice=vid)
            t = tts.StreamAdapter(tts=raw, text_pacing=True)
            t.voice_id = vid
            # Wrap with edge_tts fallback so Orpheus's intermittent
            # silent-frame bug doesn't silence the conversation.
            inners[route] = _wrap_with_edge_fallback(t)
        except Exception as e:
            logger.warning(f"[dispatch] orph tts {route}={vid} failed: {e}; will inherit TASK")

    fallback = inners.get("TASK")
    if fallback is None:
        # Last-ditch path: also wrap in StreamAdapter + edge_tts fallback
        # so even the panic fallback gets sentence-streaming and
        # auto-recovery.
        raw = LoggingGroqTTS(model="canopylabs/orpheus-v1-english", voice="troy")
        primary_panic = tts.StreamAdapter(tts=raw, text_pacing=True)
        primary_panic.voice_id = "troy"
        fallback = _wrap_with_edge_fallback(primary_panic)
        inners["TASK"] = fallback
    for route in ("BANTER", "REASONING", "EMOTIONAL"):
        inners.setdefault(route, fallback)

    return DispatchingTTS(inners=inners, fallback=fallback)
