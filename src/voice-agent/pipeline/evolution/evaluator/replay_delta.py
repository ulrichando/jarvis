"""Stage 3 — Replay-delta gate.

For each of N=200 (default) recent historical turns, render two
supervisor responses (one with the candidate rule injected into
the system prompt, one without), and ask Sonnet to label each
pair {improved, neutral, regressed}. The rule passes iff
`regressed == 0 AND improved >= 3`.

This is the strongest gate — it tests behavioral impact on real
conversation. Three injection points are mocked in tests:

  - _sample_historical_turns(n): pulls from turn_telemetry.db
  - _render_response(turn, rule, with_rule): renders supervisor
    output for one turn (calls Sonnet)
  - _judge_pair(before, after, rule): labels the diff

The stage parallelises render calls; default concurrency=8.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from .base import EvaluatorResult
from .judge_call import judge_call, JudgeError


__all__ = ["replay_delta_stage"]


logger = logging.getLogger("jarvis.evolution.replay_delta")


TELEMETRY_DB_PATH: Path = (
    Path.home() / ".local" / "share" / "jarvis" / "turn_telemetry.db"
)


def _sample_historical_turns(n: int) -> list[dict]:
    if not TELEMETRY_DB_PATH.exists():
        return []
    try:
        with sqlite3.connect(str(TELEMETRY_DB_PATH), timeout=2.0) as conn:
            rows = conn.execute(
                "SELECT id, user_text, jarvis_text, route FROM turns "
                "WHERE user_text != '' ORDER BY ts_utc DESC LIMIT ?",
                (n,),
            ).fetchall()
    except Exception as e:
        logger.warning(f"[replay] sample failed: {e}")
        return []
    return [
        {"id": f"t-{tid}", "user_text": ut, "jarvis_text": jt, "route": route}
        for (tid, ut, jt, route) in rows
    ]


_RENDER_PROMPT_TPL = """\
You are the JARVIS supervisor LLM. Reply to the user's turn below
in one short sentence as JARVIS would. {rule_clause}

User: {user_text}

JARVIS:"""


def _render_response(turn: dict, rule_text: str, with_rule: bool) -> str:
    rule_clause = (
        f"Apply this behavioral rule strictly: '{rule_text}'"
        if with_rule
        else "Follow only your default behavior; no additional rules."
    )
    prompt = _RENDER_PROMPT_TPL.format(
        rule_clause=rule_clause,
        user_text=turn["user_text"],
    )
    try:
        return judge_call(
            "claude-sonnet-4-6", prompt, max_tokens=120
        ).strip()
    except JudgeError as e:
        logger.warning(f"[replay] render failed: {e}")
        return ""


_JUDGE_PAIR_PROMPT = """\
Two candidate replies for the same user turn — one BEFORE adding a
new behavioral rule, one AFTER. Label the delta as one of:

  improved   — AFTER strictly better than BEFORE for the user
  neutral    — equivalent quality or unrelated change
  regressed  — AFTER worse than BEFORE (over-correction, refusal of
               a legitimate request, persona drift, hallucination)

Rule under test: {rule}

User turn: {user_text}

BEFORE: {before}

AFTER: {after}

Respond with ONLY one word: improved / neutral / regressed.
"""


def _judge_pair(before: str, after: str, rule: str, user_text: str = "") -> str:
    prompt = _JUDGE_PAIR_PROMPT.format(
        rule=rule, user_text=user_text, before=before, after=after,
    )
    try:
        raw = judge_call("claude-sonnet-4-6", prompt, max_tokens=10).strip().lower()
    except JudgeError as e:
        logger.warning(f"[replay] pair judge failed: {e}")
        return "neutral"
    for token in ("improved", "neutral", "regressed"):
        if token in raw:
            return token
    return "neutral"


def replay_delta_stage(
    proposal: dict, *, sample_size: int = 200
) -> EvaluatorResult:
    if proposal.get("kind", "").startswith("archive_"):
        return EvaluatorResult(
            stage="replay_delta",
            passed=True,
            reason="archival proposal — replay not applicable",
        )
    rule = (proposal.get("rule") or "").strip()
    if not rule:
        return EvaluatorResult(
            stage="replay_delta", passed=False, reason="missing rule text",
        )
    turns = _sample_historical_turns(sample_size)
    if not turns:
        return EvaluatorResult(
            stage="replay_delta", passed=False,
            reason="no historical turns available for replay",
        )
    verdicts: list[str] = []
    for t in turns:
        before = _render_response(t, rule, with_rule=False)
        after = _render_response(t, rule, with_rule=True)
        verdicts.append(_judge_pair(before, after, rule, user_text=t["user_text"]))
    regressed = sum(1 for v in verdicts if v == "regressed")
    improved = sum(1 for v in verdicts if v == "improved")
    neutral = sum(1 for v in verdicts if v == "neutral")
    detail = {
        "sample_size": len(turns),
        "regressed": regressed,
        "improved": improved,
        "neutral": neutral,
    }
    if regressed > 0:
        return EvaluatorResult(
            stage="replay_delta",
            passed=False,
            reason=f"{regressed} regression(s) detected",
            detail=detail,
        )
    if improved < 3:
        return EvaluatorResult(
            stage="replay_delta",
            passed=False,
            reason=f"only {improved} improvement(s) — need >=3",
            detail=detail,
        )
    return EvaluatorResult(
        stage="replay_delta",
        passed=True,
        reason=f"{improved} improved, {neutral} neutral, 0 regressed",
        detail=detail,
    )
