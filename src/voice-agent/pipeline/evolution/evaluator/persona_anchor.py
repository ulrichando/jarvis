"""Stage 2 — Persona-anchor protection.

Two passes:

  (a) Keyword scan. A small set of unambiguous persona terms
      (e.g., 'sir', 'pardon', "say 'yes?'") forces an immediate
      fail without spending judge tokens.

  (b) LLM judge. If the keyword scan didn't fire, ask Sonnet
      whether the rule would change identity / voice / tone, or
      contradict any of the anchor invariants. JSON response.

Either failure routes the proposal to HITL (NEEDS_REVIEW), it does
not auto-drop — the user explicitly chose 'one-tap approval for
persona changes' in the design (§3.4 of the spec).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

from .base import EvaluatorResult
from .judge_call import judge_call, JudgeError


__all__ = ["persona_anchor_stage"]


logger = logging.getLogger("jarvis.evolution.persona_anchor")


_PERSONA_KEYWORDS_RE = re.compile(
    r"\b(?:sir|pardon|yes\s+sir|\"yes,?\s*sir\"|"
    r"butler|register|tone|voice|accent|"
    r"say\s+(?:\"|')?yes(?:\"|'|\?)|"
    r"answer\s+(?:\"|')?pardon)\b",
    re.IGNORECASE,
)


_JUDGE_PROMPT_TPL = """\
You are reviewing a proposed behavioral rule for a voice assistant
named JARVIS. JARVIS's canonical persona includes:

  - Bare "Jarvis" pings reply EXACTLY "Yes?" — never "Pardon?",
    never "Yes, sir?".
  - Never appends "sir" or any honorific to replies.
  - Stays in supervisor on ambiguous input — never transfers.
  - Uses AI-native terminology ("subagent" not "specialist").
  - No mirror openers, no echo replies, no "I'm not following".

Classify the following proposed rule:

  Proposed rule: {rule}

Return ONLY a JSON object with three keys:

  is_persona: true iff the rule would change identity/voice/tone/
              register/accent/style of speech (vs. operational tool
              behavior).
  contradicts_anchor: true iff the rule contradicts any canonical
                      persona item above.
  reason: one-sentence explanation.

Example output: {{"is_persona": false, "contradicts_anchor": false, "reason": "operational rule about Chrome flags"}}
"""


def _llm_classify(rule: str) -> Optional[dict]:
    prompt = _JUDGE_PROMPT_TPL.format(rule=rule)
    try:
        raw = judge_call("claude-sonnet-4-6", prompt, max_tokens=200)
    except JudgeError as e:
        logger.warning(f"[stage:persona_anchor] judge failed: {e}")
        return None
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`").lstrip("json").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(
            f"[stage:persona_anchor] non-JSON judge response: {raw[:200]!r}"
        )
        return None


def persona_anchor_stage(proposal: dict) -> EvaluatorResult:
    rule = (proposal.get("rule") or "").strip()
    if not rule and proposal.get("kind", "").startswith("archive_"):
        return EvaluatorResult(
            stage="persona_anchor",
            passed=True,
            reason="archival proposal — anchor check not applicable",
        )

    if _PERSONA_KEYWORDS_RE.search(rule):
        return EvaluatorResult(
            stage="persona_anchor",
            passed=False,
            reason="rule matches persona/anchor-touching keyword",
            detail={"route": "HITL", "matched_by": "keyword"},
        )

    verdict = _llm_classify(rule)
    if verdict is None:
        return EvaluatorResult(
            stage="persona_anchor",
            passed=False,
            reason="judge unreachable or unparseable; routing to HITL",
            detail={"route": "HITL", "matched_by": "judge_failure"},
        )
    if verdict.get("is_persona") or verdict.get("contradicts_anchor"):
        return EvaluatorResult(
            stage="persona_anchor",
            passed=False,
            reason=f"persona/anchor judged: {verdict.get('reason', '')}",
            detail={"route": "HITL", "matched_by": "judge", "verdict": verdict},
        )
    return EvaluatorResult(
        stage="persona_anchor",
        passed=True,
        reason=f"judge ok: {verdict.get('reason', '')}",
        detail={"verdict": verdict},
    )
