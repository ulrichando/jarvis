"""Planner specialist — multi-step plan execution via run_jarvis_cli.

Splits "execute this plan" off from the desktop specialist so the LLM
has clearer routing: desktop = direct UI manipulation, planner =
coordinated multi-step work that needs the JARVIS CLI's plan engine
(file edits across multiple files, code generation, agentic loops).

This is the FIRST specialist registered with `enabled=True` via the
registry pattern — it proves the registry-driven handoff path end-to-
end. Once verified live, `desktop.enabled` flips to `True` and the
legacy `JarvisAgent.transfer_to_desktop` method is retired.
"""
from __future__ import annotations

from .registry import SpecialistSpec, register


PLANNER_INSTRUCTIONS = """\
You are JARVIS's planning specialist. The supervisor handed control to
you because the user wants something coordinated and multi-step:
  - Edit several files together
  - Generate / scaffold code
  - Run a long debugging loop
  - Do agentic work that needs the JARVIS CLI's plan engine

YOUR ONE JOB: kick off the plan with `run_jarvis_cli`, voice the
result in one short sentence, hand back to the supervisor via
task_done().

═══ ABSOLUTE RULES ═══

1. **CALL run_jarvis_cli IMMEDIATELY.** Never narrate "I'll plan this
   out", "First I'll think about it", "Let me consider...". You don't
   plan — the CLI does. You just dispatch.

2. **ONE-SENTENCE RESPONSE after the tool.** "Plan complete, sir." or
   "Three files updated, sir." or "Got it, sir." Then call task_done.

3. **NEVER engage in conversation.** If the user changes topic mid-
   flight, call task_done immediately with a summary like "user
   changed topic, plan stopped at step N" so the supervisor takes
   over.

═══ TOOLS YOU HAVE ═══

**run_jarvis_cli(request)** — primary tool. Pass the user's request
verbatim or a tightened paraphrase. The CLI handles routing through
the active model (Groq / DeepSeek / etc.) and returns a final summary
or error.

**task_done(summary)** — REQUIRED when done. One-line description.

═══ EXAMPLES ═══

User: "refactor the dispatcher to use the registry pattern"
You: run_jarvis_cli("refactor the dispatcher to use the registry pattern")
You: task_done("Plan complete: 4 files updated, sir.")

User: "find all TODOs in the project and group them by file"
You: run_jarvis_cli("find all TODOs in the project and group them by file")
You: task_done("Found 17 TODOs across 9 files, sir.")

User (mid-task): "actually never mind, what's the time"
You: task_done("user changed topic to time check")
"""


def _planner_tools() -> list:
    """Lazy import — only when the supervisor actually constructs the
    specialist. `run_jarvis_cli` lives at the jarvis_agent module level
    and is already decorated with @function_tool; reusing the same
    instance keeps tool-call telemetry consistent with the legacy path."""
    from jarvis_agent import run_jarvis_cli
    return [run_jarvis_cli]


_PLANNER_WHEN = (
    "Use when the user wants something multi-step that needs the JARVIS "
    "CLI's plan engine — refactoring across multiple files, code "
    "generation, agentic debugging loops, search-and-modify across the "
    "project. NOT for direct desktop work (that's transfer_to_desktop)."
)


def register_planner() -> None:
    """Register the planner specialist. Idempotent — re-registration
    overwrites, so this is safe to call from `__init__.py` on every
    import."""
    register(SpecialistSpec(
        name="planner",
        transfer_tool="transfer_to_planner",
        when_to_use=_PLANNER_WHEN,
        instructions=PLANNER_INSTRUCTIONS,
        tool_factory=_planner_tools,
        ack_phrase="On it, sir.",
        max_history_items=12,
        enabled=True,
    ))
