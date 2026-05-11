"""Code-reviewer subagent — second-opinion critic with clean context.

Direct copy of Cognition's "Smart Friend" pattern (per their
Multi-Agents: What's Actually Working post — clean-context reviewer
catches ~2 bugs/PR, 58% severe) and Anthropic Claude Code's built-in
`code-reviewer` Task type. The shared insight: the agent that WROTE
the code is biased toward what it already wrote; a separate reviewer
with no shared history catches what the writer missed.

Why a separate subagent (not a supervisor self-check):
  - Clean context — the supervisor's chat history + tool list pollute
    the reviewer's judgment. The reviewer gets ONLY (code, focus,
    optional context) and reasons about that.
  - Depth — uses Groq llama-3.3-70b (the TASK-tier model), not the
    fast 8b banter model. Code review is the kind of task where
    going deeper actually helps.
  - Voice-friendly — output is categorized (ISSUES / SUGGESTIONS /
    PRAISE) so the supervisor can voice the headline + severity
    before unpacking on demand.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from livekit.agents.llm import function_tool

logger = logging.getLogger("jarvis.code_reviewer")


_REVIEWER_PROMPT = """\
You are a senior code reviewer. Review the provided code for:
  1. **Bugs** — logic errors, off-by-one, null/empty handling, race
     conditions, unhandled exceptions, type mismatches.
  2. **Security** — injection vectors, secrets handling, auth bypass,
     unsafe deserialization, path traversal.
  3. **Suggestions** — clarity, naming, simplification, test gaps,
     missing edge-case handling.
  4. **Praise** — at most one short callout for something well-done.
     Skip if the code is mediocre; don't fabricate praise.

Output STRICT format (a strict reviewer is a useful reviewer):

  ISSUES (severity: high|med|low):
    - <one-line description with file:line if known>

  SUGGESTIONS:
    - <one-line description>

  PRAISE:
    - <one short line, OR omit entirely>

  VERDICT: <PASS | NEEDS_CHANGES | BLOCKED>

Rules:
  - Be specific. "Could be cleaner" is useless feedback.
  - File:line references when the input includes line numbers.
  - Don't restate the obvious. Skip findings that are personal style.
  - Maximum 5 issues + 5 suggestions; cut deeper than the top-10.
  - VERDICT=BLOCKED only for genuinely unsafe code (data loss, security
    breach, API key leak). NEEDS_CHANGES for fixable issues. PASS if
    only suggestions.
"""


def _format_for_prompt(value: Any, cap: int = 8000) -> str:
    """Cap input size — code review of >8KB is too coarse to be useful
    via a single LLM call. Use planner subagent for whole-repo work."""
    s = str(value)
    return s if len(s) <= cap else s[:cap] + "\n…(truncated, review first portion only)"


@function_tool
async def review_code(
    code: str,
    focus: str = "",
    context: str = "",
) -> str:
    """Run a code review on a snippet, returns categorized findings.

    USE WHEN:
      - User asks "review this code" / "what do you think of this" /
        "any issues with X" / "look this over" — they want a critique,
        not edits.
      - Planner just produced code work via run_jarvis_cli, and the
        user wants a second opinion before merging.

    Args:
        code: The code to review. Pass the relevant snippet, not the
              whole file — context window matters. Cap is ~8KB.
        focus: Optional focus area (e.g. "security", "performance",
               "thread-safety"). Empty = general review.
        context: Optional surrounding context (e.g. "this is a Python
                 LiveKit agent's tool wrapper" / "Bun TypeScript CLI").

    Returns:
        Structured review with ISSUES (severity-tagged), SUGGESTIONS,
        PRAISE, and VERDICT (PASS / NEEDS_CHANGES / BLOCKED).
    """
    if not os.environ.get("GROQ_API_KEY"):
        return "(reviewer offline — no GROQ_API_KEY)"

    try:
        from groq import AsyncGroq
        client = AsyncGroq(api_key=os.environ["GROQ_API_KEY"])
    except Exception as e:
        return f"(reviewer setup failed: {e})"

    user_msg_parts = []
    if context:
        user_msg_parts.append(f"CONTEXT: {context[:500]}")
    if focus:
        user_msg_parts.append(f"FOCUS: {focus[:200]}")
    user_msg_parts.append(f"CODE:\n{_format_for_prompt(code)}")
    user_msg = "\n\n".join(user_msg_parts)

    try:
        resp = await client.chat.completions.create(
            model=os.environ.get(
                "JARVIS_REVIEWER_MODEL", "llama-3.3-70b-versatile"
            ),
            messages=[
                {"role": "system", "content": _REVIEWER_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.0,
            max_tokens=900,
        )
    except Exception as e:
        logger.warning("[reviewer] LLM call failed: %s", e)
        return f"(reviewer call failed: {type(e).__name__})"

    out = resp.choices[0].message.content or ""
    logger.info("[reviewer] returned %d chars (focus=%r)", len(out), focus[:40])
    return out.strip()


def is_available() -> bool:
    """True if the reviewer can run. Mirrors validator's pattern."""
    return bool(os.environ.get("GROQ_API_KEY"))
