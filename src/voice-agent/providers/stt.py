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
  alive). If `DEEPGRAM_API_KEY` is unset entirely — or `JARVIS_DEEPGRAM_DISABLED`
  is set to deliberately stop spending Deepgram credit — the chain degrades
  gracefully to Groq Whisper Large v3 Turbo as the primary STT (the
  pre-2026-05-18 behaviour; barge-in stays alive via the VAD-direct path).

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

from providers.faster_whisper_stt import build_local_stt
from resilience import STT_BREAKER
from resilience.circuit_breaker import CircuitOpenError


logger = logging.getLogger("jarvis.stt")


# Deepgram STREAMING rejects a None/auto language with a fatal, recoverable=False
# error ("language detection is not supported in streaming mode") that tears down
# the whole AgentSession before any audio flows — the intermittent "JARVIS can't
# hear after a restart" bug. The construction-time `language=` pin in
# `_build_deepgram_stt` only protects the FIRST connect; LiveKit's FallbackAdapter
# calls `stt.stream(language=self._language)` on both its main and RECOVERY paths,
# where `self._language` can be None (the AgentSession's default). Deepgram's
# `_sanitize_options` treats None as "given" (`is_given(None)` is True) and so
# sets `config.language = LanguageCode(None)` -> None -> the fatal crash. This
# subclass closes the gap at the single chokepoint: coerce a falsy/auto language
# back to NOT_GIVEN so EVERY stream (incl. the recovery re-construct) falls through
# to the pinned construction language. Guarded import -> None (Whisper-only) when
# the plugin isn't installed, matching `_build_deepgram_stt`'s degradation.
try:
    from livekit.agents.types import NOT_GIVEN as _NOT_GIVEN
    from livekit.agents.utils import is_given as _is_given
    from livekit.plugins import deepgram as _deepgram

    class _DeepgramSTT(_deepgram.STT):
        def _sanitize_options(self, *, language=_NOT_GIVEN):
            if not _is_given(language) or not language:
                language = _NOT_GIVEN   # use the pinned construction language (en-US)
            return super()._sanitize_options(language=language)
except Exception:  # pragma: no cover - plugin not installed
    _deepgram = None
    _DeepgramSTT = None


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
    """Build a Deepgram Nova-3 streaming STT. Returns None if Deepgram is
    disabled via JARVIS_DEEPGRAM_DISABLED, if no API key is set, or if the
    import fails (so the caller can fall through to Groq Whisper alone —
    graceful degradation).

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
    # Kill-switch: skip Deepgram even when a key is present, so the chain
    # falls through to Groq Whisper Large v3 Turbo as the primary STT. Set
    # JARVIS_DEEPGRAM_DISABLED=1 to stop spending Deepgram credit while keeping
    # the key in .env for later. Default off → unchanged behaviour. Barge-in is
    # unaffected (VAD-direct since 2026-05-18); only the now-dormant
    # STT-confirmed barge-in path relied on Deepgram's streaming partials.
    if os.environ.get("JARVIS_DEEPGRAM_DISABLED", "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        logger.info(
            "[stt] JARVIS_DEEPGRAM_DISABLED set — skipping Deepgram; "
            "Groq Whisper Large v3 Turbo is the primary STT."
        )
        return None
    api_key = os.environ.get("DEEPGRAM_API_KEY")
    if not api_key:
        logger.info(
            "[stt] DEEPGRAM_API_KEY not set — falling back to Groq Whisper "
            "only (slower barge-in; final transcripts only). Set "
            "DEEPGRAM_API_KEY in src/voice-agent/.env to enable streaming STT."
        )
        return None
    if _DeepgramSTT is None:
        logger.warning(
            "[stt] livekit-plugins-deepgram not installed; "
            "falling back to Groq Whisper only. "
            "Run: pip install livekit-plugins-deepgram"
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
        return _DeepgramSTT(
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
    # Offline last rung: local faster-whisper. None unless
    # JARVIS_LOCAL_STT_ENABLED=1, so this is a no-op by default.
    local_stt = build_local_stt()

    # Ordered rungs: Deepgram (primary, streaming) → Groq Whisper Turbo →
    # local faster-whisper (offline last resort). Drop any unavailable.
    rungs = [s for s in (deepgram_stt, whisper_stt, local_stt) if s is not None]
    # Local-first override: JARVIS_LOCAL_STT_PRIMARY=1 promotes the local
    # faster-whisper rung to PRIMARY so the voice path runs on-device, with the
    # cloud STTs demoted to fallback (FallbackAdapter only cascades on failure,
    # so cloud is a safety net, not normally hit). No-op unless local_stt built.
    # NOTE: faster-whisper is finals-only (no interim transcripts) — STT-confirmed
    # barge-in is unavailable on this path; the VAD-direct interrupt still fires.
    if os.environ.get("JARVIS_LOCAL_STT_PRIMARY", "0") == "1" and local_stt is not None:
        rungs = [local_stt] + [s for s in rungs if s is not local_stt]
        logger.info("[stt] JARVIS_LOCAL_STT_PRIMARY=1 — local faster-whisper promoted to primary")
    if not rungs:  # whisper is always built — defensive
        return whisper_stt
    if len(rungs) == 1:
        # Single rung: return bare. AgentSession wraps a non-streaming STT
        # with its own StreamAdapter + VAD, so no vad is needed here.
        return rungs[0]
    # Multi-rung FallbackAdapter needs the prewarmed Silero VAD to wrap the
    # non-streaming members (Groq Whisper, faster-whisper) as streaming.
    if vad is None:
        logger.warning(
            "[stt] build_stt_chain called without vad — can't wrap non-streaming "
            "STTs into a chain; degrading to the first rung alone (%s).",
            getattr(rungs[0], "label", type(rungs[0]).__name__),
        )
        return rungs[0]
    labels = " → ".join(getattr(s, "label", type(s).__name__) for s in rungs)
    logger.info("[stt] chain: %s", labels)
    return FallbackAdapter(rungs, vad=vad)
