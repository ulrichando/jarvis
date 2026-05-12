"""Stage 3 — Replay-delta gate.

For each of N=200 (default) recent historical turns, render two
supervisor responses (one with the candidate rule injected into
the system prompt, one without), and ask Sonnet to label each
pair {improved, neutral, regressed}. The rule passes iff
`regressed == 0 AND improved >= 3`.

Parallelism: per-turn render+render+judge fan-out under
asyncio.Semaphore(concurrency=DEFAULT_CONCURRENCY=8). 200 turns
× 3 sequential calls ≈ 50 min wall-clock dropped to ~6-8 min.
Injection points (_sample_historical_turns, _render_response,
_judge_pair) stay sync — wrapped via asyncio.to_thread so existing
tests' monkey-patches continue to apply.
"""
from __future__ import annotations

import asyncio
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


DEFAULT_CONCURRENCY: int = 8


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


def _run_parallel_replay(
    turns: list[dict], rule: str, concurrency: int,
) -> list[str]:
    """Run the per-turn render+render+judge fan-out under a bounded
    semaphore. Returns verdicts in input order.

    Each turn does three sync calls (before-render, after-render,
    judge_pair). They share a semaphore so at most `concurrency`
    turns are in flight simultaneously.
    """
    async def one_turn(t: dict, sem: asyncio.Semaphore) -> str:
        async with sem:
            before = await asyncio.to_thread(_render_response, t, rule, False)
            after = await asyncio.to_thread(_render_response, t, rule, True)
            verdict = await asyncio.to_thread(
                _judge_pair, before, after, rule, t["user_text"],
            )
        return verdict

    async def gather_all() -> list[str]:
        sem = asyncio.Semaphore(max(1, concurrency))
        tasks = [one_turn(t, sem) for t in turns]
        return await asyncio.gather(*tasks)

    try:
        return asyncio.run(gather_all())
    except RuntimeError:
        # If there's already a loop in this thread (rare for a sync
        # evaluator stage but defensive), spawn a fresh loop in a
        # worker thread.
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(lambda: asyncio.run(gather_all())).result()


def replay_delta_stage(
    proposal: dict,
    *,
    sample_size: int = 200,
    concurrency: int = DEFAULT_CONCURRENCY,
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

    verdicts = _run_parallel_replay(turns, rule, concurrency)

    regressed = sum(1 for v in verdicts if v == "regressed")
    improved = sum(1 for v in verdicts if v == "improved")
    neutral = sum(1 for v in verdicts if v == "neutral")
    detail = {
        "sample_size": len(turns),
        "regressed": regressed,
        "improved": improved,
        "neutral": neutral,
        "concurrency": concurrency,
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
            reason=f"only {improved} improvement(s) — need ≥3",
            detail=detail,
        )
    return EvaluatorResult(
        stage="replay_delta",
        passed=True,
        reason=f"{improved} improved, {neutral} neutral, 0 regressed",
        detail=detail,
    )
