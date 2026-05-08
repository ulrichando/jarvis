"""Code-reviewer subagent registration.

Pattern lineage: Cognition's "Smart Friend" critic; Anthropic Claude
Code's `code-reviewer` Task type; Anthropic research-agent's citation
checker. The shared design: a separate reviewer with no shared history
catches what the writer missed (~2 bugs/PR, 58% severe per Cognition's
production data).

SubagentSpec (not SpecialistSpec) because review is one-shot — input
is (code, focus, context), output is one structured string. No
multi-turn flow needed.
"""
from __future__ import annotations

from .registry import SubagentSpec, register_subagent


CODE_REVIEWER_INSTRUCTIONS = """\
You are JARVIS's code reviewer. Your one job: call `review_code(code,
focus?, context?)` ONCE on whatever the supervisor passes you, then
report the verdict via task_done.

The supervisor delegates a string of the form:
  <CODE>...code snippet...</CODE>
  <FOCUS>optional focus area</FOCUS>     (omitted if no focus)
  <CONTEXT>optional surrounding context</CONTEXT>

Rules:
  - Pass the verdict text through verbatim; don't paraphrase. The
    supervisor decides what to voice.
  - If the input is malformed or empty, return "UNCLEAR: missing
    code to review".
  - Don't ask follow-up questions; the reviewer is single-shot.
"""


def _code_reviewer_tools() -> list:
    """Lazy import so groq client init doesn't run at registry-load.
    `task_done` is auto-attached by the framework."""
    from tools.code_reviewer import review_code
    return [review_code]


_CODE_REVIEWER_WHEN = (
    "Use when the user asks for a code review / critique / second "
    "opinion on code: \"review this\", \"what do you think of X\", "
    "\"any issues with this code\", \"look this over\". Also use "
    "after you finish multi-file code work via plan-mode + bash/edit/"
    "write, to catch what you missed (Cognition's clean-context Smart "
    "Friend pattern catches ~2 bugs/PR). Pass the code as a string. "
    "Returns categorized findings + a VERDICT (PASS|NEEDS_CHANGES|"
    "BLOCKED). Auto-disabled when GROQ_API_KEY is missing."
)


def register_code_reviewer() -> None:
    """Register the code-reviewer subagent. Auto-disables when GROQ
    key is missing (mirrors validator/browser_v2 graceful-degrade)."""
    try:
        from tools.code_reviewer import is_available
        enabled = is_available()
    except Exception:
        enabled = False

    register_subagent(SubagentSpec(
        name="code_reviewer",
        when_to_use=_CODE_REVIEWER_WHEN,
        instructions=CODE_REVIEWER_INSTRUCTIONS,
        tool_factory=_code_reviewer_tools,
        ack_phrase="Reviewing, sir.",
        max_history_items=4,  # reviewer doesn't need broader context
        enabled=enabled,
    ))
