"""Turn-lifecycle pipeline + per-route LLM/TTS dispatch.

The voice agent's turn flow is: VAD → STT → router (BANTER /
TASK / REASONING / EMOTIONAL) → dispatched LLM + TTS → telemetry.
Modules in this package own the routing, dispatch, and observability
side of that flow.

Modules:
  - dispatching_llm  : per-route LLM picker (Maya-class dispatcher with
                       FallbackAdapter to DeepSeek)
  - dispatching_tts  : per-route TTS picker
  - turn_graph       : LangGraph slow-path classifier + swap_route +
                       inject_prefix + tune_interrupt
  - turn_router      : synchronous BANTER fast-path + classifier wiring
  - turn_telemetry   : SQLite turn-record writer (~/.local/share/
                       jarvis/turn_telemetry.db) + metric helpers

Stage B reorganization 2026-05-05 (RFC-001).
"""
