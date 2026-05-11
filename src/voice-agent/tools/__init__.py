"""voice-agent tool implementations.

Each module here exposes one or more `@function_tool`-decorated callables
that the supervisor or a subagent hands to the LLM as a callable tool.
Inside this package the redundant `jarvis_` prefix is dropped — the
package namespace already supplies it.

Tools available:
  - bash              : safe shell exec (claude-code-derived)
  - browser_ext       : 38 ext_* DOM-driving tools, hub→Chrome extension WS
  - code_reviewer     : Groq-backed code review subagent
  - computer_use      : 9 desktop-control tools (xdotool / scrot / Kimi vision)
  - file_*            : direct in-process read/edit/write (claude-code-derived)
  - github            : `gh`-CLI subagent for issue / PR / repo operations
  - log_analyzer      : pattern-extracts from voice-agent log files
  - memory            : durable-fact memory (remember / recall / list / forget)
  - memory_recall     : semantic search over conversation history
  - plan_mode         : enter/exit plan mode + present_plan (claude-code-derived)
  - token_estimation  : pre-flight token-budget checker for chat_ctx
  - validator         : Groq-backed outcome validator subagent

`browser` and `browser_v2` modules were removed 2026-05-09 — the
extension-driven `browser_ext` is the only browser path now.

Stage A reorganization 2026-05-05 (RFC-001). Files were previously at
voice-agent's top level as `jarvis_<name>.py`.
"""
