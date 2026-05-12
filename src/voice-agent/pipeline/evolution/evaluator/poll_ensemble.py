"""Stage 5 — 3-of-3 unanimous PoLL ensemble.

Three judges from different model families:
  - Anthropic Sonnet 4.6
  - DeepSeek v4-pro
  - OpenAI GPT-5

Each scores the rule on three axes (aligned_with_user_pattern,
generalizable, persona_safe), each 1-5. Pass iff every judge that
responded gave >=4 on every axis AND at least 2 judges responded
(if all 3 down -> fail to HITL).

Proposer LLM (Groq llama-3.1-8b-instant) is NEVER routed here —
arXiv:2410.21819 documents self-preference bias.
"""
from __future__ import annotations

import json
import logging

from .base import EvaluatorResult
from .judge_call import judge_call, JudgeError


__all__ = ["poll_ensemble_stage", "JUDGE_MODELS"]


logger = logging.getLogger("jarvis.evolution.poll_ensemble")


JUDGE_MODELS = ["claude-sonnet-4-6", "deepseek-v4-pro", "gpt-5"]


_RUBRIC_PROMPT = """\
Score the following proposed behavioral rule for a voice assistant
(JARVIS) on three axes, each 1 (worst) to 5 (best):

  aligned_with_user_pattern — does the rule encode a real recurring
                              user expectation, not a one-off?
  generalizable             — does the rule transfer to similar
                              future requests without overfit?
  persona_safe              — is the rule safe for the canonical
                              JARVIS persona (no sir-suffix, no
                              register drift, no mirror openers)?

Rule: {rule}

Return ONLY a JSON object with the three keys + integer values.
"""


def _score_one(model: str, rule: str) -> dict | None:
    prompt = _RUBRIC_PROMPT.format(rule=rule)
    try:
        raw = judge_call(model, prompt, max_tokens=200)
    except JudgeError as e:
        logger.warning(f"[poll] {model} failed: {e}")
        return None
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`").lstrip("json").strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(f"[poll] {model} non-JSON: {raw[:200]!r}")
        return None
    if not isinstance(parsed, dict):
        return None
    return {
        "aligned_with_user_pattern": int(parsed.get("aligned_with_user_pattern", 0)),
        "generalizable": int(parsed.get("generalizable", 0)),
        "persona_safe": int(parsed.get("persona_safe", 0)),
    }


def poll_ensemble_stage(proposal: dict) -> EvaluatorResult:
    if proposal.get("kind", "").startswith("archive_"):
        return EvaluatorResult(
            stage="poll_ensemble",
            passed=True,
            reason="archival proposal — poll not applicable",
        )
    rule = (proposal.get("rule") or "").strip()
    if not rule:
        return EvaluatorResult(
            stage="poll_ensemble", passed=False, reason="missing rule",
        )

    scores: list[tuple[str, dict]] = []
    for model in JUDGE_MODELS:
        s = _score_one(model, rule)
        if s is not None:
            scores.append((model, s))
    if len(scores) < 2:
        return EvaluatorResult(
            stage="poll_ensemble",
            passed=False,
            reason=f"only {len(scores)} judge(s) responded; need >=2",
            detail={"votes_counted": len(scores), "route": "HITL"},
        )
    for model, s in scores:
        for axis in ("aligned_with_user_pattern", "generalizable", "persona_safe"):
            if s.get(axis, 0) < 4:
                return EvaluatorResult(
                    stage="poll_ensemble",
                    passed=False,
                    reason=f"{model} scored {axis}={s[axis]} < 4",
                    detail={"votes_counted": len(scores), "scores": dict(scores)},
                )
    return EvaluatorResult(
        stage="poll_ensemble",
        passed=True,
        reason=f"unanimous >=4/5 across {len(scores)} judges, all 3 axes",
        detail={"votes_counted": len(scores), "scores": dict(scores)},
    )
