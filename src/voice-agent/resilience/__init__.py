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
"""
