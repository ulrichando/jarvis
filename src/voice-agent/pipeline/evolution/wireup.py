"""On-the-turn hook + background-task entry points for the live agent.

Used by `pipeline/turn_dispatcher.py` (per-turn observer) and by
`jarvis_agent.py::entrypoint` (background mining + reporting).
All work happens off the user-facing path — exceptions are
swallowed and logged at WARNING.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from . import audit_log, batch_miner, contradiction_detector, live_capture
from .evaluator import build_default_pipeline
from .lifecycle import apply_archival_proposals, auto_stage


__all__ = [
    "observe_turn",
    "reset_for_test",
    "run_mining_cycle",
    "run_contradiction_cycle",
]


logger = logging.getLogger("jarvis.evolution.wireup")


_LIVE: Optional[live_capture.LiveCapture] = None


def _capture() -> live_capture.LiveCapture:
    global _LIVE
    if _LIVE is None:
        _LIVE = live_capture.LiveCapture()
    return _LIVE


def reset_for_test() -> None:
    global _LIVE
    _LIVE = None


def observe_turn(*, turn_id: str, user_text: str, jarvis_text: str) -> None:
    try:
        proposal = _capture().observe(
            turn_id=turn_id, user_text=user_text, jarvis_text=jarvis_text,
        )
    except Exception as e:
        logger.warning(f"[wireup] live_capture observe failed: {e}")
        return
    if proposal is None:
        return
    try:
        from .store import RuleStore
        store = RuleStore()
        logging_only = os.environ.get("JARVIS_EVOLUTION_LOGGING_ONLY", "1") == "1"
        auto_stage(store, proposal, logging_only=logging_only)
    except Exception as e:
        logger.warning(f"[wireup] auto_stage failed: {e}")


async def run_mining_cycle() -> int:
    """Mine telemetry, run each proposal through the 5-stage evaluator,
    auto-stage survivors (honouring JARVIS_EVOLUTION_LOGGING_ONLY).

    Returns the number of proposals that PASSED the evaluator and were
    sent to auto_stage (regardless of logging_only mode). The proposal
    count + per-stage outcome is recorded to the audit log so the daily
    report can summarise.
    """
    try:
        proposals = await asyncio.to_thread(batch_miner.mine, lookback_days=7)
    except Exception as e:
        logger.warning(f"[wireup] mining failed: {e}")
        return 0
    audit_log.append_event(
        kind="mining_cycle", proposal_count=len(proposals),
    )
    if not proposals:
        return 0

    from .store import RuleStore
    store = RuleStore()
    logging_only = os.environ.get("JARVIS_EVOLUTION_LOGGING_ONLY", "1") == "1"
    pipeline = build_default_pipeline()
    staged = 0
    for proposal in proposals:
        try:
            results = await asyncio.to_thread(pipeline.run, proposal)
        except Exception as e:
            logger.warning(f"[wireup] evaluator pipeline failed: {e}")
            continue
        passed = all(r.passed for r in results)
        audit_log.append_event(
            kind="mining_proposal_evaluated",
            passed=passed,
            failing_stage=(
                None if passed else next((r.stage for r in results if not r.passed), None)
            ),
            failing_reason=(
                None if passed else next((r.reason for r in results if not r.passed), None)
            ),
            rule_preview=str(proposal.get("rule", ""))[:120],
            evidence_turns=proposal.get("evidence_turns", []),
        )
        if not passed:
            continue
        try:
            auto_stage(store, proposal, logging_only=logging_only)
            staged += 1
        except Exception as e:
            logger.warning(f"[wireup] auto_stage from mining failed: {e}")
    return staged


async def run_contradiction_cycle() -> int:
    """Scan for stale/duplicate rules, route archival proposals through
    the lifecycle's bulk-retirement guard.

    Returns the count of proposals returned by the detector (NOT the
    count of actually-archived rules — the lifecycle decides what
    actually moves and what routes to HITL).
    """
    try:
        from .store import RuleStore
        store = RuleStore()
        loaded = store.load()
        proposals = contradiction_detector.run(loaded.all_rules)
    except Exception as e:
        logger.warning(f"[wireup] contradiction cycle failed: {e}")
        return 0
    if not proposals:
        return 0
    logging_only = os.environ.get("JARVIS_EVOLUTION_LOGGING_ONLY", "1") == "1"
    if logging_only:
        audit_log.append_event(
            kind="contradiction_cycle_logging_only",
            proposal_count=len(proposals),
        )
        return len(proposals)
    try:
        outcome = apply_archival_proposals(store, proposals)
        audit_log.append_event(
            kind="contradiction_cycle_applied",
            proposal_count=len(proposals),
            auto_archived=outcome.get("auto_archived", 0),
            routed_to_hitl=outcome.get("routed_to_hitl", 0),
        )
    except Exception as e:
        logger.warning(f"[wireup] apply_archival_proposals failed: {e}")
    return len(proposals)
