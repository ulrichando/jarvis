"""JARVIS specialist agent registry.

Adding a new specialist is one file:

    # src/voice-agent/specialists/myspecialist.py
    from .registry import HandoffSubagent, register

    register(HandoffSubagent(
        name="research",
        transfer_tool="transfer_to_research",
        when_to_use="multi-step web research, gathering quotes, comparing prices",
        instructions=\"\"\"You are JARVIS's research specialist. Use the
        web_search and read_url tools to gather information, then
        return a one-paragraph summary via task_done.\"\"\",
        tool_factory=lambda: [web_search, read_url],
    ))

The supervisor's `transfer_to_X` function_tools are auto-generated from
the registry — no manual handoff plumbing.

Why this design:
- Single source of truth per specialist (one .py file, ~30 lines)
- Adding a specialist doesn't touch JarvisAgent
- Spec is data, not code; future specialists could be loaded from a
  YAML/JSON manifest if we ever want runtime registration
- Lazy tool-factory pattern keeps livekit imports out of the registry
  module so tests don't need a livekit install

All specialists now live in this package; the legacy
`jarvis_specialist_agents.py` shim was retired 2026-05-01.
"""
from .registry import (
    HandoffSubagent, register, all_specs, get, clear,
    DelegatedSubagent, register_subagent, all_subagents, get_subagent, clear_subagents,
)

# Auto-register built-in specialists + subagents on package import.
# Each module's register_X() helper is idempotent (re-registration
# overwrites), so importing this package twice is safe.
def _register_builtins() -> None:
    from . import (
        desktop, browser, browser_v2,
        summarize, weather, researcher, validator, code_reviewer,
        memory_recall, github,
    )
    # planner specialist removed 2026-05-05 — replaced by in-process
    # plan-mode tools (tools/plan_mode.py). Supervisor itself enters
    # plan mode via enter_plan_mode(), drafts the plan, and executes
    # via the in-process bash/edit/write tools. Loses deepseek-v4-pro
    # for multi-step coding but gains direct execution and removes
    # the run_jarvis_cli subprocess hop.
    desktop.register_desktop()
    browser.register_browser()
    # browser_v2 self-disables when GROQ/DeepSeek key or browser-use
    # are missing — safe to always call register_browser_v2().
    browser_v2.register_browser_v2()
    # screen_share Live specialist — self-disables unless
    # JARVIS_SUBAGENT_SCREEN_SHARE=1. Uses Gemini Live RealtimeModel
    # for real-time vision during screen-share sessions.
    from . import screen_share
    screen_share.register_screen_share()
    # DelegatedSubagent path — new specialists go here so they don't bloat
    # the supervisor's prompt with one transfer_to_X tool each.
    summarize.register_summarize()
    weather.register_weather()
    researcher.register_researcher()
    # Validator + code_reviewer self-disable when GROQ key missing.
    validator.register_validator()
    code_reviewer.register_code_reviewer()
    # Memory recall self-disables when conversations.db doesn't exist.
    memory_recall.register_memory_recall()
    # GitHub self-disables when `gh` CLI not authed.
    github.register_github()

_register_builtins()

__all__ = [
    "HandoffSubagent", "register", "all_specs", "get", "clear",
    "DelegatedSubagent", "register_subagent", "all_subagents",
    "get_subagent", "clear_subagents",
]
