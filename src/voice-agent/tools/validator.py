"""Validator subagent — programmatic check that JARVIS's narrated
outcome matches the actual tool result.

Direct copy of Skyvern 2.0's Planner-Actor-Validator triad (the third
leg) and Cognition's "Smart Friend" critic pattern. Returns a
verdict + reasoning so the supervisor can either voice the result or
acknowledge the discrepancy.

Schema is reused from browser-use's `JudgementResult` Pydantic model
(verdict: bool + reasoning: str + failure_reason: str) — it's already
the de-facto standard for AI-agent validators (browser-use, Skyvern,
OpenHands all converged on the same shape).

Why a separate subagent rather than supervisor self-check:
  - Clean context — supervisor's chat history + tool list is noisy.
    Validator gets ONLY (user_request, tool_result, claimed_outcome)
    and can focus on the verification.
  - Cheap — runs Groq llama-3.1-8b-instant ($0.05/$0.08 per M tokens)
    not the 70b TASK model. Validation is a binary decision; 8b is
    plenty.
  - Voice-friendly — verdict is one bit, reasoning fits in a half
    sentence. No latency budget burnt on a long second LLM call.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from livekit.agents.llm import function_tool

logger = logging.getLogger("jarvis.validator")


_VALIDATOR_PROMPT = """\
You are a verification agent. Given:
  USER_REQUEST: what the user originally asked for
  TOOL_RESULT: the structured output the tool returned
  CLAIMED_OUTCOME: how the assistant narrated what happened

Decide whether the CLAIMED_OUTCOME accurately matches the TOOL_RESULT
relative to the USER_REQUEST.

Verdict rules:
  - verdict=true ONLY if claimed_outcome is factually consistent with
    tool_result (small wording differences are OK; outright
    contradictions or fabrications are NOT)
  - verdict=false if claimed_outcome says success when tool returned
    error, claims data the tool didn't return, or describes actions
    the tool didn't perform

Output strict JSON:
  {"verdict": true|false, "reasoning": "<1 sentence>"}

Be strict. Hallucination is the worst failure mode — when in doubt,
flag false.
"""


def _format_for_prompt(value: Any, cap: int = 800) -> str:
    """Trim arbitrary tool output for the validator prompt. Validators
    only need the gist; full payloads waste tokens and slow the call."""
    s = str(value)
    return s if len(s) <= cap else s[:cap] + "…(truncated)"


@function_tool
async def validate_outcome(
    user_request: str,
    tool_result: str,
    claimed_outcome: str,
) -> str:
    """Verify that a tool's output matches what the assistant claims
    happened. Returns a structured judgement string.

    USE WHEN:
      - You just ran a tool and want to double-check before voicing
        "Done." / "X is open" / past-tense success.
      - The user explicitly asks "are you sure" / "did that really
        work" / "verify it."
      - A previous reply got pushback ("that's not right") — call this
        before doubling down on the original answer.

    Args:
        user_request: What the user originally asked for, verbatim.
        tool_result: The raw string the tool returned (or an error
                     description).
        claimed_outcome: The proposed narration ("Chrome opened.").

    Returns:
        "VERIFIED: <reason>" if the claim matches the result.
        "FAILED: <reason>"   if there's a contradiction.
        "UNCLEAR: <reason>"  if the validator can't tell.
    """
    if not os.environ.get("GROQ_API_KEY"):
        # Graceful degrade: if we can't run the validator, return
        # UNCLEAR so the supervisor doesn't treat it as a green-light.
        return "UNCLEAR: validator offline (no GROQ_API_KEY)"

    try:
        from groq import AsyncGroq
        client = AsyncGroq(api_key=os.environ["GROQ_API_KEY"])
    except Exception as e:
        return f"UNCLEAR: validator setup failed ({e})"

    user_msg = (
        f"USER_REQUEST: {user_request[:300]}\n\n"
        f"TOOL_RESULT: {_format_for_prompt(tool_result)}\n\n"
        f"CLAIMED_OUTCOME: {claimed_outcome[:300]}"
    )

    try:
        resp = await client.chat.completions.create(
            model=os.environ.get("JARVIS_VALIDATOR_MODEL", "llama-3.1-8b-instant"),
            messages=[
                {"role": "system", "content": _VALIDATOR_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.0,
            max_tokens=160,
            response_format={"type": "json_object"},
        )
    except Exception as e:
        logger.warning("[validator] LLM call failed: %s", e)
        return f"UNCLEAR: validator call failed ({type(e).__name__})"

    raw = resp.choices[0].message.content or "{}"
    try:
        import json
        data = json.loads(raw)
        verdict = bool(data.get("verdict"))
        reasoning = str(data.get("reasoning") or "")[:200]
    except Exception:
        return f"UNCLEAR: validator returned non-json ({raw[:80]!r})"

    if verdict:
        out = f"VERIFIED: {reasoning}"
    else:
        out = f"FAILED: {reasoning}"
    logger.info("[validator] %s", out[:120])
    return out


def is_available() -> bool:
    """True if the validator can run. Mirrors browser_v2's pattern."""
    return bool(os.environ.get("GROQ_API_KEY"))
