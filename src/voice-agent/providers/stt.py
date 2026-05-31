"""STT chain — Deepgram Nova-3 (streaming) primary, Groq Whisper backup.

Why a chain (added 2026-05-18 per docs/superpowers/specs/2026-05-18-
barge-in-interrupt-fix-design.md):

  Groq Whisper Large v3 Turbo is non-streaming — it delivers transcripts
  only AFTER the user stops talking. That broke STT-confirmed barge-in
  entirely: by the time the framework knew the user had spoken, the
  utterance was complete and the framework treated it as the next turn
  instead of an interruption. Deepgram Nova-3 streams partials every
  ~150 ms over a WebSocket, so barge-in detection has a real-time
  signal to act on AND the user's turn boundaries are detected as
  speech is happening, not retroactively.

  Groq Whisper stays in the chain as the failover — if Deepgram's WS
  drops, runs out of credit, or errors, the FallbackAdapter cascades
  to Whisper and the conversation continues (slower barge-in, but
  alive). If `DEEPGRAM_API_KEY` is unset entirely, the chain degrades
  gracefully to Whisper-only (current pre-2026-05-18 behaviour).

Breaker behaviour on the STT path:
  * `_recognize_impl` is the only override — it routes the upstream
    call through the breaker so the open-circuit short-cut bypasses
    the underlying socket timeout (~30 s) and fails fast (~ms) into
    FallbackAdapter's next STT.
  * `CircuitOpenError` → `APIConnectionError`,
    `asyncio.TimeoutError` → `APITimeoutError`. Same conversion the
    other breakered provider classes use so livekit-agents' retry
    ladder handles every breaker uniformly.

Hoisted out of `jarvis_agent.py` 2026-05-10 (Step 5a of the 10/10
refactor). The class + factory are re-exported under their legacy
underscored names in jarvis_agent so the ~24 in-file references and
the existing test suite are untouched.
"""
from __future__ import annotations

import asyncio
import logging
import os

from livekit.agents import APIConnectionError, APITimeoutError
from livekit.agents.stt import FallbackAdapter
from livekit.plugins import groq

from resilience import STT_BREAKER
from resilience.circuit_breaker import CircuitOpenError


logger = logging.getLogger("jarvis.stt")


def _stt_language():
    """Return the STT language pin.

    None → auto-detect (Whisper and Deepgram both support this and
    return the detected lang code on the transcript event).

    'en' → kill-switch path, set when JARVIS_LANG_AUTODETECT is any
    falsy string (0, false, off, no, ''). Reverts to pre-spec
    behavior without a redeploy.
    """
    raw = os.environ.get("JARVIS_LANG_AUTODETECT", "1").strip().lower()
    if raw in ("0", "false", "off", "no", ""):
        return "en"
    return None


__all__ = [
    "BreakeredGroqSTT",
    "build_breakered_stt",
    "build_stt_chain",
]


class BreakeredGroqSTT(groq.STT):
    """groq.STT wrapped by `STT_BREAKER`. On `CircuitOpenError`, raises
    `APIConnectionError` so FallbackAdapter (if any STT fallback is
    configured) takes over without waiting the full upstream timeout."""

    async def _recognize_impl(self, *args, **kw):
        try:
            return await STT_BREAKER.call(super()._recognize_impl, *args, **kw)
        except CircuitOpenError as e:
            raise APIConnectionError() from e
        except asyncio.TimeoutError:
            # Breaker's own 8 s timeout fired (separate from the
            # underlying STT's timeout). Surface as `APITimeoutError`
            # so livekit-agents' retry / fallback path handles it
            # uniformly with other timeout sources.
            raise APITimeoutError() from None

    async def _call_with_breaker_for_test(self):
        """Test seam — instance method so the test exercises
        `build_breakered_stt()` construction, catching factory regressions
        (wrong model string, broken constructor signature) at test time
        rather than at production startup. The body itself only probes
        the breaker-open path; production calls go through
        `_recognize_impl`."""
        async def _no_op():
            return None
        try:
            return await STT_BREAKER.call(_no_op)
        except CircuitOpenError as e:
            raise APIConnectionError() from e
        except asyncio.TimeoutError:
            raise APITimeoutError() from None


def build_breakered_stt() -> BreakeredGroqSTT:
    """Constructor used by the JarvisAgent wiring at session.start().

    When _stt_language() is None (auto-detect default), we pass
    detect_language=True instead of language=None — Groq's OpenAI-
    compatible wrapper calls LanguageCode(language) unconditionally
    and LanguageCode(None) raises AttributeError. detect_language=True
    internally sets language="" (empty string), which LanguageCode
    accepts and which tells Whisper to auto-detect.

    When _stt_language() is "en" (kill-switch), we pin language="en"
    with detect_language=False (Groq default) for pre-spec behaviour.
    """
    lang = _stt_language()
    if lang is None:
        return BreakeredGroqSTT(model="whisper-large-v3-turbo", detect_language=True)
    return BreakeredGroqSTT(model="whisper-large-v3-turbo", language=lang)


# Deepgram Nova-3 keyterm prompting — boosts recognition of specific
# terms at the STT level. Added 2026-05-20 after the echo-vs-accent
# telemetry diagnosis found JARVIS's "misheard me" turns were dominated
# by genuine recognition errors (e.g. "Joris"/"Jervis" for "Jarvis"),
# NOT echo. A wrong-but-plausible English word can't be caught by any
# downstream garbage-gate (stt_gate.py), so the only lever is upstream
# recognition. Nova-3 only (the plugin's _validate_keyterm rejects
# keyterm on other models, and rejects `keywords` on Nova-3). Extend
# with your own names / domain vocab via JARVIS_STT_KEYTERMS
# (comma-separated) — keep it focused; over-long lists dilute the boost.
_DEFAULT_KEYTERMS: tuple[str, ...] = ("Jarvis",)


def _stt_keyterms() -> list[str]:
    """The Deepgram keyterm boost list: built-in defaults plus any
    operator-supplied terms from JARVIS_STT_KEYTERMS (comma-separated).
    De-duplicated case-insensitively, first-seen order preserved. Read
    at call time so the value can change across worker restarts."""
    terms = list(_DEFAULT_KEYTERMS)
    terms += [t.strip() for t in os.environ.get("JARVIS_STT_KEYTERMS", "").split(",")]
    out: list[str] = []
    seen: set[str] = set()
    for t in terms:
        if not t:
            continue
        k = t.lower()
        if k not in seen:
            seen.add(k)
            out.append(t)
    return out


def _build_deepgram_stt():
    """Build a Deepgram Nova-3 streaming STT. Returns None if no API
    key is set or if the import fails (so the caller can fall through
    to Groq Whisper alone — graceful degradation).

    Configuration tuned for barge-in responsiveness:
      - model="nova-3-general" — latest model as of 2026-05-18.
      - interim_results=True — partial transcripts every ~150 ms
        (the whole point of swapping STT).
      - no_delay=True — emit each chunk immediately, don't batch
        for "natural" sentence breaks.
      - endpointing_ms=300 — turn-end after 300 ms of silence.
      - smart_format=True — punctuation + capitalization in transcripts.
      - sample_rate=16000 — matches Silero VAD + the LiveKit audio
        track's downsampled rate.
      - keyterm=_stt_keyterms() — Nova-3 keyterm prompting; boosts
        "Jarvis" (+ operator vocab) so accent mishears resolve at STT.
    """
    api_key = os.environ.get("DEEPGRAM_API_KEY")
    if not api_key:
        logger.info(
            "[stt] DEEPGRAM_API_KEY not set — falling back to Groq Whisper "
            "only (slower barge-in; final transcripts only). Set "
            "DEEPGRAM_API_KEY in src/voice-agent/.env to enable streaming STT."
        )
        return None
    try:
        from livekit.plugins import deepgram
    except ImportError as e:
        logger.warning(
            f"[stt] livekit-plugins-deepgram not installed ({e}); "
            f"falling back to Groq Whisper only. "
            f"Run: pip install livekit-plugins-deepgram"
        )
        return None
    # Deepgram STREAMING does NOT support language auto-detection (unlike Groq
    # Whisper): a None/auto language raises "language detection is not supported
    # in streaming mode" in SpeechStream.__init__, which kills every session
    # (stt_error, recoverable=False). _stt_language() returns None for the
    # auto-detect default, so pin a concrete language for Deepgram — default
    # en-US, override via JARVIS_DEEPGRAM_LANGUAGE. The Whisper fallback rung
    # keeps auto-detect for non-English.
    dg_language = (
        os.environ.get("JARVIS_DEEPGRAM_LANGUAGE", "").strip()
        or _stt_language()
        or "en-US"
    )
    try:
        return deepgram.STT(
            model="nova-3-general",
            language=dg_language,
            interim_results=True,
            no_delay=True,
            endpointing_ms=300,
            smart_format=True,
            sample_rate=16000,
            keyterm=_stt_keyterms(),
            api_key=api_key,
        )
    except Exception as e:
        logger.warning(
            f"[stt] Deepgram STT construction failed ({type(e).__name__}: {e}); "
            f"falling back to Groq Whisper only."
        )
        return None


def build_stt_chain(vad=None):
    """Build the production STT chain — Deepgram primary, Groq Whisper
    failover. Falls through to Whisper-only when Deepgram is unavailable
    (no API key, plugin missing, construction error).

    Returns a `FallbackAdapter` when multi-provider, a single STT when
    only Whisper is available. Both shapes are accepted by AgentSession's
    `stt=` parameter.

    `vad` is the prewarmed Silero VAD instance (from
    `proc.userdata["vad"]`). The FallbackAdapter uses it to auto-wrap
    the non-streaming Groq Whisper with `stt.StreamAdapter` so the
    chain treats both providers uniformly. Required when Deepgram is
    in the chain; ignored when returning Whisper alone.
    """
    deepgram_stt = _build_deepgram_stt()
    whisper_stt = build_breakered_stt()
    if deepgram_stt is None:
        return whisper_stt
    if vad is None:
        logger.warning(
            "[stt] build_stt_chain called without vad — Whisper can't be "
            "wrapped as streaming; degrading to Deepgram-only (no failover)."
        )
        return deepgram_stt
    logger.info("[stt] chain: Deepgram Nova-3 (primary) → Groq Whisper Turbo (backup)")
    return FallbackAdapter([deepgram_stt, whisper_stt], vad=vad)
