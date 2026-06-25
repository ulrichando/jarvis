"""Self-evolution trigger — JARVIS proposes improvements when there is work.

Drives the existing pipeline periodically in AUTO mode, **proposal-only** (never
merges, never deploys, never restarts anything):

    evolution cycle (detect -> self-assess -> priority queue -> build/retry)
    -> publish (gated AUTOPUBLISH)                -> GitHub PRs for review

Deploy happens ONLY later, when you approve a proposal in /evolution — and even
then the Phase-1 watchdog auto-rolls-back anything unhealthy.

Guards (skip the run):
  * manual mode is active (AUTO flag absent),
  * an active-deploy marker exists (a deploy is mid-verification), or
  * you were recently active (a turn within JARVIS_EVOLUTION_NIGHTLY_TURN_GUARD_S)
    — proposal generation is for quiet gaps.

Conservative by default: installing/enabling the timer does nothing beyond
logging "manual-mode" until the web /evolution page or state file enables AUTO.

Run by ``jarvis-evolution-nightly.timer`` (historical name); safe to run by hand.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict

from pipeline.automod import deploy as _deploy
from pipeline.automod import _state
from pipeline.automod import fault_boundary

logger = logging.getLogger("jarvis.automod.nightly")

# If a turn fired within this window, you're awake/using JARVIS → skip the run.
TURN_GUARD_S = int(os.environ.get("JARVIS_EVOLUTION_NIGHTLY_TURN_GUARD_S", "900"))


def _user_recently_active() -> bool:
    age = _deploy._seconds_since_last_turn()
    return age is not None and age < TURN_GUARD_S


def _autopublish() -> bool:
    return os.environ.get("JARVIS_EVOLUTION_AUTOPUBLISH", "0") == "1"


@fault_boundary.supervised("nightly_run", fallback=lambda: {"crashed": True})
def run() -> Dict[str, Any]:
    """One nightly pass. Returns a small summary dict (also for logs/tests).

    Never raises — any failure is logged and reported in the dict so the timer
    unit always exits cleanly."""
    # Guard 1: never run while a deploy is being health-checked.
    if _deploy.read_marker():
        logger.info("[nightly] active-deploy marker present — skipping")
        return {"skipped": "deploy-in-flight"}

    # Guard 2: manual mode is the default. The timer may be enabled, but it
    # only builds proposals when AUTO mode is explicitly selected.
    if not _state.is_auto_mode():
        logger.info("[nightly] manual mode — skipping automatic cycle")
        return {"skipped": "manual-mode", "mode": "manual"}

    # Guard 3: only run when you're away.
    if _user_recently_active():
        logger.info("[nightly] a turn fired within %ds — user active, skipping", TURN_GUARD_S)
        return {"skipped": "user-active"}

    summary: Dict[str, Any] = {"mode": "auto", "detected": 0, "spawned": 0, "published": 0}

    # 1. Run the same evolution cycle the web "Run now" button uses. It detects,
    #    self-assesses, builds P0->P3, retries failures until functional, and
    #    stops at the shared daily budget.
    try:
        from pipeline.automod import cycle
        cycle_summary = cycle.run_cycle(detect_first=True, assess_first=True)
        summary.update(cycle_summary)
        summary["spawned"] = int(cycle_summary.get("spawned", 0) or 0)
        summary["detected"] = int(cycle_summary.get("detected", 0) or 0)
    except Exception as e:  # noqa: BLE001
        logger.warning("[nightly] cycle failed: %s", e)
        summary["cycle_error"] = str(e)

    # 2. Publish freshly-spawned, not-yet-published proposals (gated).
    if summary["spawned"] and _autopublish():
        try:
            from pipeline.automod import cli, publish
            for art in cli.cmd_list(only_pending=True):
                if art.get("pr_url"):
                    continue
                ok, info = publish.publish(art["id"])
                if ok:
                    summary["published"] += 1
                    logger.info("[nightly] published %s → %s", art.get("id"), info)
                else:
                    logger.warning("[nightly] publish %s failed: %s", art.get("id"), info)
        except Exception as e:  # noqa: BLE001
            logger.warning("[nightly] publish step failed: %s", e)
            summary["publish_error"] = str(e)

    # 3. Clean up old artifacts so the auto-mods dir doesn't grow unbounded.
    try:
        from pipeline.automod.artifact import cleanup_artifacts
        summary["cleaned"] = cleanup_artifacts()
    except Exception as e:  # noqa: BLE001
        logger.warning("[nightly] artifact cleanup failed: %s", e)

    # 3b. Prune orphan automod/* branches (failed/abandoned builds whose
    # in-worktree branch delete silently no-opped). Keeps the branch namespace
    # from piling up into 'branch already exists' collisions on rebuild.
    try:
        from pipeline.automod.spawner import prune_orphan_branches
        summary["branches_pruned"] = prune_orphan_branches()
    except Exception as e:  # noqa: BLE001
        logger.warning("[nightly] branch prune failed: %s", e)

    logger.info(
        "[nightly] done: detected=%s spawned=%s published=%s cleaned=%s",
        summary["detected"], summary["spawned"],
        summary["published"], summary.get("cleaned", 0),
    )
    return summary


def main() -> int:
    import json
    print(json.dumps(run()))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
