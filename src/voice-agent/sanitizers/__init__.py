"""LLM output-stream sanitizers + provider-format patches.

These modules monkey-patch livekit-agents stream classes to fix bugs
that originate in upstream LLM output (malformed tool names, leaked
tool-call envelopes as text, structured-output corruption) plus
provider-specific compat shims (DeepSeek-shape reasoning_content
round-trip).

Modules:
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
                         handoff stream so the framework's specialist-
                         spoken outcome is what reaches TTS
                         (was handoff_text_suppressor.py)
  - pycall             : detects tool-call-as-text leaks (`<function...
                         >` pattern) and blanks the leaked content
                         (was pycall_sanitizer.py)
  - tool_name          : recovers the real tool name when the LLM emits
                         a malformed/typo'd tool name; soft-recovers if
                         the tool requires runtime context
                         (was tool_name_sanitizer.py)

Stage B reorganization 2026-05-05 (RFC-001).
"""
