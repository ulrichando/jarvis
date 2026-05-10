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
from resilience.circuit_breaker import CircuitBreaker

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
STT_BREAKER = CircuitBreaker("stt", fail_threshold=3, cooldown_s=20, timeout_s=8)
TTS_BREAKER = CircuitBreaker("tts", fail_threshold=3, cooldown_s=20, timeout_s=8)
LLM_BREAKER = CircuitBreaker("llm", fail_threshold=2, cooldown_s=30, timeout_s=12)
