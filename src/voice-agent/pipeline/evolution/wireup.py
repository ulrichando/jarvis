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
from .lifecycle import auto_stage


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
    try:
        proposals = await asyncio.to_thread(batch_miner.mine, lookback_days=7)
    except Exception as e:
        logger.warning(f"[wireup] mining failed: {e}")
        return 0
    audit_log.append_event(kind="mining_cycle", proposal_count=len(proposals))
    return len(proposals)


async def run_contradiction_cycle() -> int:
    try:
        from .store import RuleStore
        store = RuleStore()
        loaded = store.load()
        proposals = contradiction_detector.run(loaded.all_rules)
    except Exception as e:
        logger.warning(f"[wireup] contradiction cycle failed: {e}")
        return 0
    return len(proposals)
