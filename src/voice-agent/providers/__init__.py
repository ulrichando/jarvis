"""Provider adapters wrapping livekit-plugins-* with project-specific
resilience plumbing (per-provider circuit breakers, fallback cascades).

Modules:
  - stt : on-device faster-whisper (Deepgram optional cloud rung)
          wrapped by `STT_BREAKER`
  - tts : local Kokoro/Piper + Edge-TTS fallback chain with `TTS_BREAKER`
  - llm : multi-provider dispatcher build (Anthropic/DeepSeek/OpenAI/Kimi)
          with breakered streams

All breaker singletons come from `resilience` (per the 2026-05-10
hoist) so each provider module can be imported without pulling in
jarvis_agent.
"""
