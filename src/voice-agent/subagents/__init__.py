"""JARVIS subagent agent registry.

Adding a new subagent is one file:

    # src/voice-agent/subagents/mysubagent.py
    from .registry import HandoffSubagent, register

    register(HandoffSubagent(
        name="research",
        transfer_tool="transfer_to_research",
        when_to_use="multi-step web research, gathering quotes, comparing prices",
        instructions=\"\"\"You are JARVIS's research subagent. Use the
        web_search and read_url tools to gather information, then
        return a one-paragraph summary via task_done.\"\"\",
        tool_factory=lambda: [web_search, read_url],
    ))

The supervisor's `transfer_to_X` function_tools are auto-generated from
the registry — no manual handoff plumbing.

Why this design:
- Single source of truth per subagent (one .py file, ~30 lines)
- Adding a subagent doesn't touch JarvisAgent
- Spec is data, not code; future subagents could be loaded from a
  YAML/JSON manifest if we ever want runtime registration
- Lazy tool-factory pattern keeps livekit imports out of the registry
  module so tests don't need a livekit install

All subagents now live in this package; the legacy
`jarvis_subagent_agents.py` shim was retired 2026-05-01.
"""
from .registry import (
    HandoffSubagent, register, all_specs, get, clear,
    DelegatedSubagent, register_subagent, all_subagents, get_subagent, clear_subagents,
)

# Auto-register built-in handoff + delegated subagents on package
# import. Each module's register_X() helper is idempotent
# (re-registration overwrites), so importing this package twice
# is safe. HandoffSubagent specs each expose a `transfer_to_X`
# function_tool on the supervisor; DelegatedSubagent specs all
# share a single `delegate(role, task)` tool so adding one doesn't
# bloat the supervisor's prompt with another transfer_to_X.
def _register_builtins() -> None:
    from . import (
        desktop, browser,
        summarize, weather, researcher, validator, code_reviewer,
        memory_recall, github,
    )
    # planner subagent removed 2026-05-05 — replaced by in-process
    # plan-mode tools (tools/plan_mode.py). Supervisor itself enters
    # plan mode via enter_plan_mode(), drafts the plan, and executes
    # via the in-process bash/edit/write tools. Loses deepseek-v4-pro
    # for multi-step coding but gains direct execution and removes
    # the run_jarvis_cli subprocess hop.
    # browser_v2 retired 2026-05-12 — disabled twin with three known
    # unfixed bugs (CDP attach, Groq json_schema, actions[-1] subscript).
    # Voice agent has one browser subagent, period: subagents/browser.py.
    desktop.register_desktop()
    browser.register_browser()
    # screen_share Live subagent — self-disables unless
    # JARVIS_SUBAGENT_SCREEN_SHARE=1. Uses Gemini Live RealtimeModel
    # for real-time vision during screen-share sessions.
    from . import screen_share
    screen_share.register_screen_share()
    # DelegatedSubagent path — new delegated subagents go here so they
    # don't bloat the supervisor's prompt with one transfer_to_X tool
    # each. All seven share the single `delegate(role, task)` tool.
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
