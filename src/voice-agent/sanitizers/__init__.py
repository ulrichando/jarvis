"""LLM output-stream sanitizers + provider-format patches.

These modules monkey-patch livekit-agents stream classes to fix bugs
that originate in upstream LLM output (malformed tool names, leaked
tool-call envelopes as text, structured-output corruption) plus
provider-specific compat shims (DeepSeek-shape reasoning_content
round-trip).

Modules:
  - anthropic_strict_schema : walks every Anthropic tool schema and
                         forces `additionalProperties: false` on every
                         object node — required because Anthropic
                         rejects tools without it, and strict_schema_relax
                         (load-bearing for Groq) emits legacy shapes
                         that don't set it (live failure 2026-05-11)
  - deepseek_cache_tokens : backfills DeepSeek's `prompt_cache_hit_tokens`
                         into the OpenAI-spec
                         `prompt_tokens_details.cached_tokens` slot when
                         the latter is empty, so LLMMetrics.prompt_cached_tokens
                         (and our turn-telemetry column) lands the value.
                         Defensive — DeepSeek currently mirrors both
                         fields, so this only kicks in on future API
                         shifts or DeepSeek-compatible third-party
                         endpoints. Gates on base_url=deepseek.com.
  - deepseek_roundtrip : echoes `reasoning_content` on assistant tool-call
                         messages so DeepSeek-style providers don't
                         reject the tool result as malformed
  - denial_detector    : suppresses memory-capability denial phrases
                         ('I'm a conversational AI, I don't retain
                         information') from supervisor output (Phase 4
                         of memory-layer fix)
  - dsml               : extracts inline tool-call envelopes (U+FF5C
                         delimiter) and re-emits them as proper
                         FunctionToolCall chunks (was dsml_sanitizer.py)
  - handoff_text       : suppresses supervisor text content during a
                         handoff stream so the framework's subagent-
                         spoken outcome is what reaches TTS
                         (was handoff_text_suppressor.py)
  - pycall             : detects tool-call-as-text leaks (`<function...
                         >` pattern) and blanks the leaked content
                         (was pycall_sanitizer.py)
  - strict_schema_relax: forces every tool through the legacy schema
                         generator (no additionalProperties:false, no
                         function.strict=True) so Groq/Moonshot accept
                         mixed defaults+required tools
  - tool_name          : recovers the real tool name when the LLM emits
                         a malformed/typo'd tool name; soft-recovers if
                         the tool requires runtime context
                         (was tool_name_sanitizer.py)

Stage B reorganization 2026-05-05 (RFC-001).
"""
