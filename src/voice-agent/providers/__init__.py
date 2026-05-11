"""Provider adapters wrapping livekit-plugins-* with project-specific
diagnostic shims (Groq error-body logging) and resilience plumbing
(per-provider circuit breakers, fallback cascades).

Modules:
  - stt : Groq Whisper STT wrapped by `STT_BREAKER`
  - tts : Groq Orpheus TTS with error-body logger + `TTS_BREAKER`
  - llm : Groq Llama LLM with breakered streams + dispatcher build

All breaker singletons come from `resilience` (per the 2026-05-10
hoist) so each provider module can be imported without pulling in
jarvis_agent.
"""
