"""Resilience primitives for the voice service.

These modules harden the voice pipeline against transient failures:
provider outages, network blips, deadlocked event loops, stale track
events from livekit during reconnect.

Modules:
  - circuit_breaker     : closed/open/half-open state machine with
                          probe + cooldown; instantiated for STT/TTS/LLM
  - llm_idle_timeout    : per-stream `_run` wrap that bounds upstream
                          stalls (raises asyncio.TimeoutError)
  - reconnect_ladder    : two-tier resume → full-teardown → SystemExit
                          escalation for the voice-client peer
  - track_guard         : monkey-patches livekit.rtc.Room._on_room_event
                          to swallow KeyError on stale track SIDs during
                          reconnect (was livekit_track_guard.py)
  - watchdog            : sd_notify(WATCHDOG=1) heartbeat loop +
                          STOPPING on shutdown event

Stage B reorganization 2026-05-05 (RFC-001).

Breaker singletons (2026-05-10): the three breakers gating Groq STT /
TTS / LLM endpoints live here at module scope. Pre-2026-05-10 they
were instantiated in jarvis_agent.py and the TTS / STT / LLM provider
classes (also in jarvis_agent.py) referenced them via module globals.
Now that the providers are moving to their own modules (Step 5/6 of
the 10/10 refactor), the breakers must be importable from a stable
location to avoid circular imports.
"""
from __future__ import annotations

from resilience.circuit_breaker import CircuitBreaker


def _is_expected_provider_error(exc: BaseException) -> bool:
    """Return True for upstream "expected" errors that should NOT count
    toward the breaker's failure threshold. The FallbackAdapter rotates
    providers on these; tripping the breaker only blocks recovery.

    Recognized signals (matched against the full __cause__ / __context__
    chain since livekit-agents wraps everything as APIConnectionError):

      - Groq rate-limit-exceeded 429 ("Rate limit reached for ... TPM")
      - Validation errors from upstream tool-call malformation
        ("failed to call a function", "tool call validation failed")

    Billing exhaustion (credit_exhausted / insufficient_quota) is
    intentionally EXCLUDED — it doesn't self-heal, so it should trip the
    breaker and route around the dead provider (see note below).

    Walks the exception chain so wrapped errors are still matched.
    Added 2026-05-16 per global review §P0-16 + audio review §P0;
    promotes the inline validation-error revert from BreakeredLLMStream
    into a single source of truth.
    """
    msgs: list[str] = []
    cur: BaseException | None = exc
    seen: set[int] = set()
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        msgs.append(str(cur).lower())
        cur = cur.__cause__ or cur.__context__
    blob = " | ".join(msgs)
    return any(s in blob for s in (
        # Validation errors — LLM emitted a malformed tool call. The
        # tool_name_sanitizer / downstream recovery handle the malform.
        "failed to call a function",
        "tool call validation failed",
        "failed_generation",
        "please adjust your prompt",
        # Rate-limit — provider-side, recovers on its own within seconds,
        # so don't burn the breaker on a transient 429.
        "rate_limit_exceeded",
        "rate limit reached",
        "tokens per min",
        "requests per min",
    ))
    # NOTE: credit_exhausted / insufficient_quota are deliberately NOT in
    # the non-failure set. Unlike rate limits they do NOT self-heal within
    # the breaker's cooldown — so they SHOULD trip the breaker, which makes
    # the FallbackAdapter skip the exhausted provider for the cooldown
    # window instead of paying a failed round-trip to it on every call for
    # the entire outage. (Was previously misclassified as transient.)


# Per-upstream circuit breakers. A DNS / API blip on one upstream
# (e.g. STT) no longer drags TTS + LLM down with a 30-s timeout each.
# CircuitOpenError gets converted to APIConnectionError at the call
# site so the FallbackAdapter chain takes over within ms instead of
# waiting for the OS socket timeout.
#
# Tuning history:
#   - STT/TTS: fail_threshold=3, cooldown_s=20, timeout_s=8 (default
#     since 2026-05-04). Three failures in a row almost always means
#     the endpoint is genuinely broken; cooling down 20 s lets it
#     recover without permanent shutout.
#   - LLM: fail_threshold=2, cooldown_s=30, timeout_s=12. LLM stalls
#     are more expensive (each timeout costs the user 12 s of silence)
#     so the threshold is tighter.
#   - 2026-05-16: all three now classify rate-limit + validation errors
#     as non-failures so a single 429 doesn't open the breaker for 30s.
_BREAKER_KW = dict(non_failure_classifier=_is_expected_provider_error)
STT_BREAKER = CircuitBreaker("stt", fail_threshold=3, cooldown_s=20, timeout_s=8, **_BREAKER_KW)
TTS_BREAKER = CircuitBreaker("tts", fail_threshold=3, cooldown_s=20, timeout_s=8, **_BREAKER_KW)
LLM_BREAKER = CircuitBreaker("llm", fail_threshold=2, cooldown_s=30, timeout_s=12, **_BREAKER_KW)
