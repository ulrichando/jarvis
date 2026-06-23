"""Autonomous evolution build cycle (2026-06-23).

Builds queued self-evolution intents ONE AT A TIME. On a failed build it logs the
error, learns from it, and re-queues the SAME goal with a different, narrower
approach — up to patterns.MAX_RETRY_ATTEMPTS total attempts per goal. Passing
builds become `pending` proposals for human review (this cycle never deploys).
Honors the pause flag between every build, so it can be stopped cleanly.

Run via bin/jarvis-evolution-cycle (detached). Long-running: each build spawns a
coding-agent in an isolated worktree (minutes).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time

from pipeline.automod import artifact, patterns
from pipeline.automod._state import (
    artifact_path,
    is_evolution_paused,
    queue_path,
)

logger = logging.getLogger("jarvis.automod.cycle")


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _enqueue(intent: dict) -> None:
    qp = queue_path()
    qp.parent.mkdir(parents=True, exist_ok=True)
    with qp.open("a", encoding="utf-8") as f:
        f.write(json.dumps(intent, ensure_ascii=False) + "\n")


async def _build(intent_id: str) -> dict | None:
    """Build one intent synchronously (the spawn awaits finalize). Returns its
    artifact dict, or None if nothing was written."""
    from pipeline.automod.spawner import drain_queue
    await drain_queue(only_id=intent_id, force=True)
    try:
        return artifact.load(intent_id)
    except Exception:  # noqa: BLE001
        return None


def _build_with_retries(intent_id: str) -> dict:
    """Build one goal, retrying with a new approach on each failure up to the
    attempt cap. Returns an outcome dict. Logs every error."""
    current = intent_id
    last_reason = ""
    for _ in range(patterns.MAX_RETRY_ATTEMPTS):
        if is_evolution_paused():
            return {"id": current, "status": "paused"}
        art = asyncio.run(_build(current))
        if art is None:
            artifact.audit("automod_build_error", id=current, reason="no artifact written")
            logger.warning("[cycle] no artifact for %s", current)
            return {"id": current, "status": "error", "reason": "no artifact written"}
        status = art.get("status")
        if status == "pending":
            artifact.audit("automod_build_pending", id=current)
            logger.info("[cycle] build pending (ready for review): %s", current)
            return {"id": current, "status": "pending"}
        # failed → log the error, then learn + retry with a different approach
        last_reason = str(art.get("rejection_reason", ""))
        artifact.audit("automod_build_failed", id=current, reason=last_reason,
                       attempt=art.get("attempt", 1))
        logger.warning("[cycle] build FAILED id=%s attempt=%s reason=%s",
                       current, art.get("attempt", 1), last_reason)
        retry = patterns.build_retry_intent(art)
        if not retry:
            artifact.audit("automod_build_exhausted", id=current, reason=last_reason)
            logger.warning("[cycle] giving up on %s after %s: %s",
                           current, art.get("attempt", 1), last_reason)
            return {"id": current, "status": "exhausted", "reason": last_reason}
        # Mark the failed artifact retried so the nightly scanner won't double-retry.
        try:
            art["retried"] = True
            artifact_path(current).write_text(
                json.dumps(art, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
        _enqueue(retry)
        current = retry["id"]
    return {"id": current, "status": "exhausted", "reason": last_reason or "max attempts"}


def run_cycle(*, assess_first: bool = True, max_intents: int | None = None) -> dict:
    """Assess (queue improvements), then build the queue one goal at a time with
    learn-and-retry. Returns a summary. Never raises."""
    summary: dict = {"built": [], "assessed_queued": 0, "paused": False}
    if is_evolution_paused():
        logger.info("[cycle] paused — not starting")
        return {**summary, "paused": True, "skipped": "paused"}

    # This IS the explicit build actuator → force spawn-live for this process.
    os.environ["JARVIS_AUTOMOD_SPAWN_LIVE"] = "1"

    if assess_first:
        try:
            from pipeline.automod import introspection
            res = introspection.run_self_assessment()
            summary["assessed_queued"] = int(res.get("queued", 0) or 0)
        except Exception as e:  # noqa: BLE001
            logger.warning("[cycle] self-assessment failed: %s", e)

    from pipeline.automod.spawner import _read_queue
    # Build in priority order — P0 first (self-assessment improvements rank P0;
    # 'P0' < 'P1' < … sorts correctly as strings). Retries inherit their rank.
    queue = sorted(_read_queue(), key=lambda r: str(r.get("priority", "P3")))
    seen: set[str] = set()
    processed = 0
    for intent in queue:
        if is_evolution_paused():
            summary["paused"] = True
            break
        if max_intents is not None and processed >= max_intents:
            break
        lineage = str(intent.get("lineage") or intent.get("id") or "")
        if not lineage or lineage in seen:
            continue
        seen.add(lineage)
        summary["built"].append(_build_with_retries(intent["id"]))
        processed += 1

    artifact.audit("automod_cycle_complete",
                   built=len(summary["built"]), paused=summary["paused"])
    logger.info("[cycle] complete: built=%d paused=%s",
                len(summary["built"]), summary["paused"])
    return summary


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    max_intents = int(argv[0]) if argv and argv[0].isdigit() else None
    print(json.dumps(run_cycle(max_intents=max_intents)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
