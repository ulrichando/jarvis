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

Telemetry callbacks:
  - `record_synthesis(session, input_chars, audio_bytes)` (from
    pipeline.barge_in) writes a row to the per-turn position table.
  - `_active_session_for_telemetry[0]` (in jarvis_agent) holds the
    live AgentSession so `record_synthesis` can find it.

The chunked-stream class imports both inside `_run` so this module
doesn't pull jarvis_agent into scope at import time.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time

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
from providers.piper_tts import build_local_tts

from pipeline.dispatching_tts import DispatchingTTS
from pipeline.settings import read_unified_setting
from resilience import TTS_BREAKER
from resilience.circuit_breaker import CircuitOpenError


logger = logging.getLogger("jarvis.tts")


__all__ = [
    "LoggingGroqChunkedStream",
    "LoggingGroqTTS",
    "build_tts_chain",
    "build_dispatching_tts",
]


# ── Groq TTS error-body logging shim ─────────────────────────────────

class LoggingGroqChunkedStream(_GroqChunkedStream):
    async def _run(self, output_emitter) -> None:
        # `record_synthesis` lives in pipeline.barge_in — direct import,
        # no circular risk. `_active_session_for_telemetry` still lives
        # in jarvis_agent (session-bound state); lazy-imported to dodge
        # circular-load risk at module init.
        from pipeline.barge_in import record_synthesis
        from jarvis_agent import _active_session_for_telemetry

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
            record_synthesis(
                _active_session_for_telemetry[0],
                len(self._input_text or ""),
                nonlocal_audio_bytes[0],
            )
            return

        # Echo-aware barge-in: record the text JARVIS is about to speak so the
        # gate can tell the user's real speech from JARVIS's own echo on a hot
        # mic (pipeline/echo_gate consumers read it via speaking_tracker).
        try:
            from pipeline import speaking_tracker
            speaking_tracker.note_speaking(self._input_text or "")
        except Exception:
            pass
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
            _stream_start = time.monotonic()  # for cancel-latency log
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
                    try:
                        async for data, _ in resp.content.iter_chunks():
                            output_emitter.push(data)
                            nonlocal_audio_bytes[0] += len(data)
                        output_emitter.flush()
                    except asyncio.CancelledError:
                        # Barge-in fired — framework cancelled _run() at
                        # the task level. Close the aiohttp response
                        # immediately so the Groq Orpheus socket aborts
                        # instead of streaming the full WAV that we'd
                        # just drop. This is the difference between
                        # JARVIS stopping in ~300 ms (target) vs ~1-3 s
                        # (current symptom — observed live, see
                        # docs/superpowers/specs/2026-05-18-barge-in-
                        # interrupt-fix-design.md). The async-with on
                        # the response below WILL close it eventually,
                        # but only after the current chunk-read syscall
                        # returns — proactive close() kills the socket
                        # mid-read and the kernel sends RST to Groq.
                        elapsed_ms = (time.monotonic() - _stream_start) * 1000
                        logger.info(
                            "[tts] Orpheus cancelled after %.0fms (%d bytes, voice=%s)",
                            elapsed_ms, nonlocal_audio_bytes[0], self._opts.voice,
                        )
                        resp.close()
                        raise
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
            logger.info(
                "[tts] Orpheus rendered %d bytes (voice=%s, text=%r)",
                nonlocal_audio_bytes[0], self._opts.voice,
                (self._input_text or "")[:40],
            )
        except CircuitOpenError as e:
            logger.warning("[tts] Orpheus skipped — breaker open; FallbackAdapter will use EdgeTTS")
            raise APIConnectionError() from e
        except asyncio.TimeoutError:
            logger.warning("[tts] Orpheus TIMEOUT — FallbackAdapter will use EdgeTTS")
            raise APITimeoutError() from None
        except Exception as e:
            logger.warning("[tts] Orpheus FAILED (%s: %s) — FallbackAdapter will use EdgeTTS",
                           type(e).__name__, str(e)[:120])
            raise
        # Record this synthesize() call's position-table entry. Runs ONLY
        # on success path — on breaker exception above, the audio wasn't
        # actually played so we don't append.
        record_synthesis(
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

        # 2026-05-28 boundary-gate against leaks that bypass the
        # streaming `_parse_choice` sanitizer (e.g., model output that
        # arrives via a non-LLMStream code path; observed live with
        # JARVIS voicing "(ambient — not directed at me)" stage-
        # directions despite the soul/supervisor prompt + the regex
        # patch in _leak_shapes.META_SILENCE_RE). Apply the canonical
        # sanitizer right at the TTS boundary so anything that reaches
        # here gets a final check before audio rendering.
        #
        # `sanitize_text_for_tts` returns "" for matched leak shapes,
        # which makes Orpheus render no audio (logged as "0 bytes"
        # downstream); the user hears silence — the correct outcome.
        try:
            from sanitizers.pycall import sanitize_text_for_tts
            cleaned = sanitize_text_for_tts(text or "")
            if cleaned != text:
                logger.warning(
                    "[tts] boundary-gate suppressed leak text "
                    "(was=%r, now=%r)",
                    (text or "")[:80], cleaned[:80],
                )
            text = cleaned
        except Exception as e:
            # Fail open — never block TTS on a sanitizer crash.
            logger.debug("[tts] boundary-gate sanitizer skipped: %s", e)

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
    local_primary = os.environ.get("JARVIS_LOCAL_TTS_PRIMARY", "0") == "1"
    local_only    = os.environ.get("JARVIS_LOCAL_TTS_ONLY", "0") == "1"

    # The tray pick (~/.jarvis/tts-provider) is AUTHORITATIVE for the engine,
    # via the spec prefix — so a Kokoro pick runs on-device, a groq/edge pick
    # runs that online engine. The old code only ever built Orpheus here and
    # ignored the engine, which silently moved the voice off Kokoro whenever
    # JARVIS_PIN_ALL_ROUTES disabled the per-route dispatcher.
    provider, voice = "", ""
    spec = read_unified_setting("tts-provider", tts_provider_file)
    if spec and ":" in spec:
        provider, voice = (s.strip() for s in spec.split(":", 1))

    # The on-device rung (Kokoro/Piper). build_local_tts() reads its voice from
    # JARVIS_LOCAL_TTS_VOICE, kept in sync with the tray's Kokoro pick via the
    # voice-mode env preset — so a "kokoro:<voice>" spec resolves to that voice.
    local = build_local_tts()

    def _orpheus(v):
        return LoggingGroqTTS(model="canopylabs/orpheus-v1-english", voice=v or groq_voice)

    def _edge(v):
        return edge_tts_plugin.EdgeTTS(voice=v or edge_voice)

    # Strict-local: on-device only, no cloud rungs (mirrors the dispatcher's
    # JARVIS_LOCAL_TTS_ONLY). A local-engine failure then has no fallback — the
    # deliberate "strictly on-device" trade.
    if local_only and local is not None:
        logger.info("[tts] JARVIS_LOCAL_TTS_ONLY=1 — on-device TTS only (no cloud fallback)")
        return [local]

    # Spec prefix picks the PRIMARY engine.
    primary, primary_engine = None, None
    if provider == "kokoro" and local is not None:
        primary, primary_engine = local, "kokoro"
        logger.info(f"[tts] Kokoro on-device primary [tray selection: {spec}]")
    elif provider == "groq":
        primary, primary_engine = _orpheus(voice), "groq"
        logger.info(f"[tts] Groq Orpheus voice={voice or groq_voice} primary [tray selection]")
    elif provider == "edge":
        primary, primary_engine = _edge(voice), "edge"
        logger.info(f"[tts] Edge-TTS voice={voice or edge_voice} primary [tray selection]")

    if primary is None:
        # No usable engine pick → local-first when requested, else Orpheus.
        if local_primary and local is not None:
            primary, primary_engine = local, "kokoro"
            logger.info("[tts] Kokoro on-device primary [local-first default]")
        else:
            primary, primary_engine = _orpheus(groq_voice), "groq"
            logger.info(f"[tts] Groq Orpheus voice={groq_voice} primary [default]")

    # Append the OTHER engines as fallback rungs (resilience), skipping the one
    # already primary. Order: Orpheus → Edge → local (offline). Best-effort: a
    # missing key for an UNSELECTED engine (e.g. no GROQ_API_KEY on a Kokoro-only
    # box) drops that rung instead of crashing the whole TTS chain.
    chain = [primary]
    if primary_engine != "groq":
        try:
            chain.append(_orpheus(groq_voice))
        except Exception as e:
            logger.warning(f"[tts] Orpheus fallback unavailable ({e})")
    if primary_engine != "edge":
        try:
            chain.append(_edge(edge_voice))
        except Exception as e:
            logger.warning(f"[tts] Edge fallback unavailable ({e})")
    if primary_engine != "kokoro" and local is not None:
        chain.append(local)
    return chain


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

    # Offline last rung shared across every route's chain. None unless
    # JARVIS_LOCAL_TTS_ENABLED=1 (then Piper, fully local). Lets each
    # route keep a voice when both Orpheus AND Edge-TTS (network) are down.
    _local_fallback = build_local_tts()
    if _local_fallback is not None:
        _engine = os.environ.get("JARVIS_LOCAL_TTS_ENGINE", "piper").strip() or "piper"
        _local_fallback.voice_id = f"{_engine}:local"
    # Local-first: JARVIS_LOCAL_TTS_PRIMARY=1 promotes the local TTS (Kokoro/Piper)
    # to PRIMARY on every route so the voice path runs on-device; Orpheus + Edge
    # become fallbacks. No-op unless the local rung is built.
    _local_primary = (
        os.environ.get("JARVIS_LOCAL_TTS_PRIMARY", "0") == "1"
        and _local_fallback is not None
    )
    if _local_primary:
        logger.info("[dispatch] JARVIS_LOCAL_TTS_PRIMARY=1 — local TTS promoted to primary on all routes")
    # Strict local: JARVIS_LOCAL_TTS_ONLY=1 drops EVERY cloud/network rung
    # (Orpheus AND Edge-TTS) so TTS is 100% on-device — the mirror of the STT
    # JARVIS_STT_LOCAL_ONLY policy. A local-engine failure then has NO fallback
    # (loud: logged below + silence), which is the deliberate "strictly local"
    # trade the user chose. No-op unless the local rung built. Implies local-first.
    _local_only = (
        os.environ.get("JARVIS_LOCAL_TTS_ONLY", "0") == "1"
        and _local_fallback is not None
    )
    if _local_only:
        _local_primary = True
        _only_engine = os.environ.get("JARVIS_LOCAL_TTS_ENGINE", "piper").strip() or "piper"
        logger.warning(
            "[dispatch] JARVIS_LOCAL_TTS_ONLY=1 — cloud TTS fallback removed "
            "(Orpheus + Edge dropped); on-device %s only. A local-engine failure "
            "has NO fallback by design.", _only_engine,
        )

    inners: dict[str, object] = {}
    fallback = None

    def _wrap_with_edge_fallback(primary):
        """Wrap a per-route TTS in a FallbackAdapter: primary → Edge-TTS →
        local Piper (offline). When the primary returns no audio frames
        (Orpheus/EL intermittent) or the network is down, the next rung
        takes over. Preserves the .voice_id attribute the DispatchingTTS
        exposes for telemetry."""
        if _local_only:
            # Strict local: ignore the cloud `primary`; return the on-device
            # TTS alone (no FallbackAdapter → no Orpheus, no Edge). Shared
            # across all four routes, same as the local fallback already is.
            return _local_fallback
        rungs = [primary]
        if _edge_fallback is not None:
            rungs.append(_edge_fallback)
        if _local_fallback is not None:
            rungs.append(_local_fallback)
        # Local-first promotion: move the local TTS to the front so it's the
        # route primary (Orpheus + Edge demoted to fallbacks). The FallbackAdapter
        # auto-wraps the non-streaming local TTS in StreamAdapter for per-sentence
        # synthesis; Orpheus stays in the chain so its upstream-cancel stays live.
        if _local_primary:
            rungs = [_local_fallback] + [r for r in rungs if r is not _local_fallback]
        if len(rungs) == 1:
            return primary
        try:
            wrapped = tts.FallbackAdapter(rungs)
            wrapped.voice_id = getattr(rungs[0], "voice_id", getattr(primary, "voice_id", "?"))
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

    # French inner — EdgeTTS with a French voice. Constructed once,
    # used by DispatchingTTS.pick(route, lang='fr') regardless of
    # route. Defaults to fr-FR-HenriNeural (male, standard French);
    # override via JARVIS_FR_EDGE_VOICE.
    if _local_only:
        # French EdgeTTS is Microsoft cloud — dropped under strict-local. The
        # 'fr' pick falls back to the on-device English chain (af_heart reading
        # French) rather than reaching the network.
        _fr_inner = None
    else:
        fr_voice = os.environ.get("JARVIS_FR_EDGE_VOICE", "fr-FR-HenriNeural")
        try:
            _fr_inner = edge_tts_plugin.EdgeTTS(voice=fr_voice)
            _fr_inner.voice_id = f"edge:{fr_voice[:18]}…"
        except Exception as e:
            logger.warning(
                f"[dispatch] French edge_tts construction failed ({e}); "
                f"fr will fall back to English chain"
            )
            _fr_inner = None

    return DispatchingTTS(inners=inners, fallback=fallback, fr_inner=_fr_inner)
