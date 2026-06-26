"""Autonomous evolution build cycle (2026-06-23).

Builds queued self-evolution intents ONE AT A TIME. On a failed build it logs the
error, learns from it, and re-queues the SAME goal with a different, narrower
approach. Passing builds become `pending` proposals for human review (this cycle
never deploys). Honors the pause flag between every build, so it can be stopped
cleanly. The daily evolution budget is enforced by throttle.py; failures keep
retrying across cycles/days until they become functional.

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

from pipeline.automod import artifact, fault_boundary, patterns
from pipeline.automod._state import (
    artifact_path,
    cycle_marker_path,
    is_evolution_paused,
    queue_path,
)
from pipeline.automod import throttle

logger = logging.getLogger("jarvis.automod.cycle")

# Circuit-breaker: stop retrying a goal after this many attempts, and never retry
# a blocklist rejection (the agent keeps trying to edit its own protected code).
# Without this, _build_until_functional retried until the daily budget ran out —
# the "RETRY attempt 5/6" storms that wasted the whole cap on one doomed goal.
MAX_RETRY_ATTEMPTS = int(os.environ.get("JARVIS_AUTOMOD_MAX_RETRY_ATTEMPTS", "2"))


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _already_queued(text: str) -> bool:
    """True if a pending queue entry already has this exact intent text."""
    qp = queue_path()
    if not text or not qp.exists():
        return False
    try:
        for ln in qp.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                if (json.loads(ln).get("intent") or "").strip() == text:
                    return True
            except json.JSONDecodeError:
                continue
    except OSError:
        return False
    return False


def _enqueue(intent: dict) -> None:
    qp = queue_path()
    qp.parent.mkdir(parents=True, exist_ok=True)
    # Dedup: skip a NON-retry intent whose exact text is already pending — stops
    # the assessment piling the same goal up. RETRY intents are EXEMPT (they are
    # meant to re-attempt, and are bounded by MAX_RETRY_ATTEMPTS + the `retried`
    # marker), so legitimate retries of different goals are never dropped.
    text = (intent.get("intent") or "").strip()
    if text and not text.upper().startswith("RETRY") and _already_queued(text):
        logger.info("[cycle] skip enqueue — intent already queued: %s", text[:80])
        return
    with qp.open("a", encoding="utf-8") as f:
        f.write(json.dumps(intent, ensure_ascii=False) + "\n")


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _acquire_cycle_marker() -> tuple[bool, str]:
    marker = cycle_marker_path()
    marker.parent.mkdir(parents=True, exist_ok=True)
    if marker.exists():
        try:
            data = json.loads(marker.read_text(encoding="utf-8"))
            pid = int(data.get("pid", 0) or 0)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pid = 0
        if pid and _pid_alive(pid):
            return False, "cycle-running"
        marker.unlink(missing_ok=True)
    marker.write_text(
        json.dumps({"pid": os.getpid(), "started_at": _now_iso()}),
        encoding="utf-8",
    )
    return True, ""


def _release_cycle_marker() -> None:
    marker = cycle_marker_path()
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
        if int(data.get("pid", 0) or 0) != os.getpid():
            return
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return
    marker.unlink(missing_ok=True)


async def _build(intent_id: str) -> tuple[dict | None, bool]:
    """Build one intent synchronously (the spawn awaits finalize). Returns its
    artifact dict plus whether a build actually launched."""
    from pipeline.automod.spawner import drain_queue
    spawned = await drain_queue(only_id=intent_id, force=False)
    if spawned <= 0:
        return None, False
    try:
        return artifact.load(intent_id), True
    except Exception:  # noqa: BLE001
        return None, True


def _build_until_functional(intent_id: str) -> dict:
    """Build one goal, carrying its P0-P3 priority forward through retries until
    it becomes pending, the cycle is paused, or today's budget is exhausted."""
    current = intent_id
    last_reason = ""
    attempts = 0
    while True:
        if is_evolution_paused():
            return {"id": current, "status": "paused", "attempts": attempts}
        if throttle.remaining_today() <= 0:
            return {
                "id": current,
                "status": "budget-exhausted",
                "attempts": attempts,
                "reason": "daily_cap_reached",
            }
        art, spawned = asyncio.run(_build(current))
        if not spawned:
            reason = "daily_cap_reached" if throttle.remaining_today() <= 0 else "no spawn"
            return {"id": current, "status": "deferred", "attempts": attempts, "reason": reason}
        attempts += 1
        if art is None:
            artifact.audit("automod_build_error", id=current, reason="no artifact written")
            logger.warning("[cycle] no artifact for %s", current)
            return {"id": current, "status": "error", "attempts": attempts,
                    "reason": "no artifact written"}
        status = art.get("status")
        if status == "pending":
            artifact.audit("automod_build_pending", id=current)
            logger.info("[cycle] build pending (ready for review): %s", current)
            return {"id": current, "status": "pending", "attempts": attempts}
        # failed → log the error, then learn + retry with a different approach
        last_reason = str(art.get("rejection_reason", ""))
        artifact.audit("automod_build_failed", id=current, reason=last_reason,
                       attempt=art.get("attempt", 1))
        logger.warning("[cycle] build FAILED id=%s attempt=%s reason=%s",
                       current, art.get("attempt", 1), last_reason)
        # CIRCUIT-BREAKER (2026-06-25): halt the retry chain instead of burning
        # the whole daily budget on one doomed goal. Stop after MAX_RETRY_ATTEMPTS,
        # and NEVER retry a blocklist rejection — a retry just hits the blocklist
        # again. Kills the "RETRY attempt 5/6" storms in the Failed list.
        this_attempt = int(art.get("attempt", 1) or 1)
        blocklisted = "blocklist" in last_reason.lower()
        if this_attempt >= MAX_RETRY_ATTEMPTS or blocklisted:
            halt = "blocklist (no retry)" if blocklisted else f"retry cap {MAX_RETRY_ATTEMPTS} reached"
            artifact.audit("automod_retry_halted", id=current, reason=halt, attempt=this_attempt)
            logger.info("[cycle] circuit-breaker halted retries for %s — %s", current, halt)
            return {"id": current, "status": "failed", "attempts": attempts,
                    "reason": last_reason, "halted": halt}
        retry = patterns.build_retry_intent(art)
        if not retry:
            artifact.audit("automod_build_error", id=current,
                           reason=f"retry intent unavailable: {last_reason}")
            return {"id": current, "status": "error", "attempts": attempts,
                    "reason": last_reason or "retry intent unavailable"}
        # Mark the failed artifact retried so the nightly scanner won't double-retry.
        try:
            art["retried"] = True
            artifact_path(current).write_text(
                json.dumps(art, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
        _enqueue(retry)
        if throttle.remaining_today() <= 0:
            artifact.audit("automod_build_retry_queued", id=current,
                           retry_id=retry["id"], reason=last_reason)
            return {"id": current, "status": "retry-queued", "next_id": retry["id"],
                    "attempts": attempts, "reason": last_reason}
        current = retry["id"]


@fault_boundary.supervised("cycle_run", fallback=lambda: {
    "built": [], "detected": 0, "assessed_queued": 0, "paused": False,
    "crashed": True,
})
def run_cycle(
    *,
    detect_first: bool = True,
    assess_first: bool = True,
    max_intents: int | None = None,
) -> dict:
    """Assess (queue improvements), then build the queue one goal at a time with
    learn-and-retry. Returns a summary. Never raises."""
    summary: dict = {
        "built": [],
        "detected": 0,
        "assessed_queued": 0,
        "paused": False,
        "budget": {
            "cap": throttle.daily_cap(),
            "admitted_today": throttle.admitted_today(),
            "remaining": throttle.remaining_today(),
        },
    }
    if is_evolution_paused():
        logger.info("[cycle] paused — not starting")
        return {**summary, "paused": True, "skipped": "paused"}
    acquired, skipped = _acquire_cycle_marker()
    if not acquired:
        logger.info("[cycle] %s — not starting", skipped)
        return {**summary, "skipped": skipped}

    try:
        # This IS the explicit build actuator → enable spawning for this process.
        os.environ["JARVIS_AUTOMOD_SPAWN_LIVE"] = "1"

        if detect_first:
            try:
                summary["detected"] = patterns.scan_and_emit()
            except Exception as e:  # noqa: BLE001
                summary["detect_error"] = str(e)
                logger.warning("[cycle] detector failed: %s", e)

        if assess_first:
            try:
                from pipeline.automod import introspection
                res = introspection.run_self_assessment()
                summary["assessed_queued"] = int(res.get("queued", 0) or 0)
            except Exception as e:  # noqa: BLE001
                logger.warning("[cycle] self-assessment failed: %s", e)

        from pipeline.automod.spawner import _read_queue
        # Build in priority order — P0 first. Retries inherit their rank, so a
        # failed P0 keeps the front of the line until it becomes functional.
        queue = sorted(_read_queue(), key=lambda r: str(r.get("priority", "P3")))
        seen: set[str] = set()
        processed = 0
        for intent in queue:
            if is_evolution_paused():
                summary["paused"] = True
                break
            if throttle.remaining_today() <= 0:
                break
            if max_intents is not None and processed >= max_intents:
                break
            lineage = str(intent.get("lineage") or intent.get("id") or "")
            if not lineage or lineage in seen:
                continue
            seen.add(lineage)
            outcome = fault_boundary.run_unit(
                f"build:{intent['id']}",
                lambda iid=intent["id"]: _build_until_functional(iid),
                on_error=lambda exc, iid=intent["id"]: {
                    "id": iid, "status": "error",
                    "reason": f"exception:{exc}"[:200],
                },
            )
            summary["built"].append(outcome)
            processed += 1
            if outcome.get("status") in {"budget-exhausted", "retry-queued"}:
                break

        summary["spawned"] = sum(int(x.get("attempts", 0) or 0) for x in summary["built"])
        summary["budget"] = {
            "cap": throttle.daily_cap(),
            "admitted_today": throttle.admitted_today(),
            "remaining": throttle.remaining_today(),
        }
        artifact.audit("automod_cycle_complete",
                       built=len(summary["built"]), spawned=summary["spawned"],
                       paused=summary["paused"])
        logger.info("[cycle] complete: built=%d spawned=%d paused=%s remaining=%d",
                    len(summary["built"]), summary["spawned"], summary["paused"],
                    summary["budget"]["remaining"])
        return summary
    finally:
        _release_cycle_marker()


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    max_intents = int(argv[0]) if argv and argv[0].isdigit() else None
    print(json.dumps(run_cycle(max_intents=max_intents)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
