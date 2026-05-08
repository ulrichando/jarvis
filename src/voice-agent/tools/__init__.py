"""voice-agent tool implementations.

Each module here exposes one or more `@function_tool`-decorated callables
that the supervisor or a specialist hands to the LLM as a callable tool.
Inside this package the redundant `jarvis_` prefix is dropped — the
package namespace already supplies it.

Tools available:
  - browser           : ext-bridge driver for the legacy browser specialist
  - browser_ext       : 37 ext_* DOM-driving tools (legacy specialist)
  - browser_v2        : autonomous browser-use agent (currently disabled,
                        see specialists/browser_v2.py:108-125)
  - code_reviewer     : Groq-backed code review subagent
  - computer_use      : 9 desktop-control tools (xdotool / scrot / Gemini Vision)
  - github            : `gh`-CLI subagent for issue / PR / repo operations
  - log_analyzer      : pattern-extracts from voice-agent log files
  - memory            : durable-fact memory (remember / recall / list)
  - memory_recall     : semantic search over conversation history
  - validator         : Groq-backed outcome validator subagent

Stage A reorganization 2026-05-05 (RFC-001). Files were previously at
voice-agent's top level as `jarvis_<name>.py`. Backward-compat shims at
the old paths log a `jarvis.layout-shim` debug line on import and
re-export the public surface; shims will be removed after 7 days of
zero shim-hit telemetry (W-007a.2).
"""
