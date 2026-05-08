"""Validator subagent — wraps `validate_outcome` as a SubagentSpec
routable via the supervisor's `delegate(role, task)` tool.

Pattern lineage: Skyvern 2.0 Planner-Actor-Validator (the third leg);
Cognition's "Smart Friend" critic; Anthropic research-agent's citation
checker. The shared insight across these systems: a separate verifier
with clean context catches roughly half of an executor's regressions
(Cognition: ~2 bugs/PR caught, 58% severe).

Why SubagentSpec (not SpecialistSpec): validation is one-shot — input
is (user_request, tool_result, claimed_outcome), output is one string.
No multi-turn flow. SubagentSpec lets it ride the existing
delegate(role, task) plumbing without bloating the supervisor's tool
list.
"""
from __future__ import annotations

from .registry import SubagentSpec, register_subagent


VALIDATOR_INSTRUCTIONS = """\
You are JARVIS's verification agent. Your one job: programmatically
check whether a claimed outcome matches what a tool actually did.

The supervisor delegates a string of the form:
  <USER_REQUEST>...</USER_REQUEST>
  <TOOL_RESULT>...</TOOL_RESULT>
  <CLAIMED_OUTCOME>...</CLAIMED_OUTCOME>

You MUST call `validate_outcome(user_request, tool_result, claimed_outcome)`
exactly once with those three fields, then report back via task_done
with the validator's verdict string verbatim.

Rules:
  - Don't paraphrase the verdict — pass it through. The supervisor
    decides what to voice based on VERIFIED / FAILED / UNCLEAR.
  - Don't reason about it yourself; the inner validator already
    reasoned about it. Your job is wiring, not analysis.
  - If the input doesn't contain all three fields, return
    "UNCLEAR: missing input fields".
"""


def _validator_tools() -> list:
    """Lazy import — keeps the Groq client out of the registry-import
    path. `task_done` is auto-attached by the framework."""
    from tools.validator import validate_outcome
    return [validate_outcome]


_VALIDATOR_WHEN = (
    "Verify that a tool's actual output matches the assistant's "
    "claimed outcome — call BEFORE voicing any past-tense success "
    "(\"Done\", \"opened\", \"posted\", \"sent\"). Especially after "
    "tools that have a confabulation history: launch_app, ext_*, "
    "transfer_to_browser, run_jarvis_cli. The validator returns one "
    "of VERIFIED / FAILED / UNCLEAR plus a one-sentence reason. "
    "Pass: user_request, tool_result, claimed_outcome."
)


def register_validator() -> None:
    """Register the validator subagent. Auto-disables when GROQ key
    missing (validator can't run without it)."""
    try:
        from tools.validator import is_available
        enabled = is_available()
    except Exception:
        enabled = False

    register_subagent(SubagentSpec(
        name="validator",
        when_to_use=_VALIDATOR_WHEN,
        instructions=VALIDATOR_INSTRUCTIONS,
        tool_factory=_validator_tools,
        ack_phrase="Verifying, sir.",
        max_history_items=4,  # validator only needs the recent turn
        enabled=enabled,
    ))
