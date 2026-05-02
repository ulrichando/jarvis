"""JARVIS specialist agent registry.

Adding a new specialist is one file:

    # src/voice-agent/specialists/myspecialist.py
    from .registry import SpecialistSpec, register

    register(SpecialistSpec(
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
    SpecialistSpec, register, all_specs, get, clear,
    SubagentSpec, register_subagent, all_subagents, get_subagent, clear_subagents,
)

# Auto-register built-in specialists + subagents on package import.
# Each module's register_X() helper is idempotent (re-registration
# overwrites), so importing this package twice is safe.
def _register_builtins() -> None:
    from . import (
        desktop, planner, browser, browser_v2,
        summarize, weather, researcher, validator,
    )
    desktop.register_desktop()
    planner.register_planner()
    browser.register_browser()
    # browser_v2 self-disables when GROQ/DeepSeek key or browser-use
    # are missing — safe to always call register_browser_v2().
    browser_v2.register_browser_v2()
    # SubagentSpec path — new specialists go here so they don't bloat
    # the supervisor's prompt with one transfer_to_X tool each.
    summarize.register_summarize()
    weather.register_weather()
    researcher.register_researcher()
    # Validator self-disables when GROQ key missing.
    validator.register_validator()

_register_builtins()

__all__ = [
    "SpecialistSpec", "register", "all_specs", "get", "clear",
    "SubagentSpec", "register_subagent", "all_subagents",
    "get_subagent", "clear_subagents",
]
