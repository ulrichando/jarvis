"""Nightly self-evolution trigger — JARVIS proposes improvements while you sleep.

Drives the existing pipeline in a quiet window, **proposal-only** (never merges,
never deploys, never restarts anything):

    detector (patterns.scan_and_emit)            → queue.jsonl
    → spawner.drain_queue (gated SPAWN_LIVE)      → proposal branches + artifacts
    → publish (gated AUTOPUBLISH)                 → GitHub PRs for review

Deploy happens ONLY later, when you approve a proposal in /evolution — and even
then the Phase-1 watchdog auto-rolls-back anything unhealthy.

Guards (skip the run):
  * an active-deploy marker exists (a deploy is mid-verification), or
  * you were recently active (a turn within JARVIS_EVOLUTION_NIGHTLY_TURN_GUARD_S)
    — the nightly is for when you're away.

Conservative by default — SHADOW mode:
  * the detector always runs (harmless: just records candidate intents), but
  * the coding-agent spawn is OFF unless ``JARVIS_AUTOMOD_SPAWN_LIVE=1``, and
  * auto-opening PRs is OFF unless ``JARVIS_EVOLUTION_AUTOPUBLISH=1``.
So installing/enabling the timer does nothing risky until you flip those.

Run by ``jarvis-evolution-nightly.timer``; safe to run by hand.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict

from pipeline.automod import deploy as _deploy

logger = logging.getLogger("jarvis.automod.nightly")

# If a turn fired within this window, you're awake/using JARVIS → skip the run.
TURN_GUARD_S = int(os.environ.get("JARVIS_EVOLUTION_NIGHTLY_TURN_GUARD_S", "900"))


def _user_recently_active() -> bool:
    age = _deploy._seconds_since_last_turn()
    return age is not None and age < TURN_GUARD_S


def _autopublish() -> bool:
    return os.environ.get("JARVIS_EVOLUTION_AUTOPUBLISH", "0") == "1"


def run() -> Dict[str, Any]:
    """One nightly pass. Returns a small summary dict (also for logs/tests).

    Never raises — any failure is logged and reported in the dict so the timer
    unit always exits cleanly."""
    # Guard 1: never run while a deploy is being health-checked.
    if _deploy.read_marker():
        logger.info("[nightly] active-deploy marker present — skipping")
        return {"skipped": "deploy-in-flight"}

    # Guard 2: only run when you're away.
    if _user_recently_active():
        logger.info("[nightly] a turn fired within %ds — user active, skipping", TURN_GUARD_S)
        return {"skipped": "user-active"}

    summary: Dict[str, Any] = {"detected": 0, "spawned": 0, "published": 0}

    # 1. Detector → queue. Always safe; in shadow mode this is the only effect
    #    (candidate intents accumulate for inspection).
    try:
        from pipeline.automod.patterns import scan_and_emit
        summary["detected"] = scan_and_emit()
    except Exception as e:  # noqa: BLE001
        logger.warning("[nightly] detector failed: %s", e)
        summary["detect_error"] = str(e)

    # 2. Spawn proposals (no-op unless JARVIS_AUTOMOD_SPAWN_LIVE=1).
    #    SAFETY: the coding-agent wrapper (bin/jarvis-automod-impl) does
    #    `git checkout master` + branches to automod/<id> and does NOT restore
    #    the branch. The live agent runs from this working tree, so leaving it on
    #    a proposal branch (built off origin/master) would make the NEXT restart
    #    load stale code + lose unpushed work. We snapshot the branch and restore
    #    it after spawning, so the live tree always returns to where it was.
    orig_branch = _deploy._git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    # SAFETY: the coding-agent wrapper runs `git reset --hard origin/master`,
    # which would DESTROY any uncommitted work in the live tree (e.g. a feature
    # you're mid-edit on). Stash it first, restore it after. The stash is a safe
    # holding pen — even if the pop conflicts, the work survives in `git stash`.
    dirty = bool(_deploy._git("status", "--porcelain").stdout.strip())
    stashed = False
    if dirty:
        sr = _deploy._git("stash", "push", "-u", "-m", "evolution-nightly-autostash")
        stashed = sr.returncode == 0 and "No local changes" not in (sr.stdout or "")
        if not stashed:
            logger.error("[nightly] could not stash a dirty tree — aborting to avoid data loss")
            summary["skipped"] = "dirty-tree-unstashable"
            return summary
    try:
        from pipeline.automod.spawner import drain_queue
        summary["spawned"] = asyncio.run(drain_queue())
    except Exception as e:  # noqa: BLE001
        logger.warning("[nightly] spawn failed: %s", e)
        summary["spawn_error"] = str(e)
    finally:
        # 1. Restore the branch the live tree was on.
        if orig_branch and orig_branch != "HEAD":
            cur = _deploy._git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
            if cur != orig_branch:
                r = _deploy._git("checkout", orig_branch)
                if r.returncode == 0:
                    logger.info("[nightly] restored working tree: %s → %s", cur, orig_branch)
                else:
                    logger.error("[nightly] FAILED to restore branch %s (on %s): %s",
                                 orig_branch, cur, r.stderr.strip())
                    summary["branch_restore_error"] = r.stderr.strip()
        # 2. Restore the stashed uncommitted work.
        if stashed:
            pr = _deploy._git("stash", "pop")
            if pr.returncode == 0:
                logger.info("[nightly] restored stashed uncommitted work")
            else:
                logger.error("[nightly] stash pop FAILED — your work is safe in "
                             "`git stash list`: %s", pr.stderr.strip())
                summary["stash_pop_error"] = pr.stderr.strip()

    # 3. Publish freshly-spawned, not-yet-published proposals (gated).
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

    logger.info(
        "[nightly] done: detected=%s spawned=%s published=%s",
        summary["detected"], summary["spawned"], summary["published"],
    )
    return summary


def main() -> int:
    import json
    print(json.dumps(run()))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
