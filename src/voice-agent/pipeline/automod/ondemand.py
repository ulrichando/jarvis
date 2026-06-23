"""On-demand self-evolution runner.

Launched detached by propose_code_mod after an explicit self-improvement/code-mod
request. It processes one queued intent by id, using the same worktree-isolated
spawner as the periodic scheduler, then optionally publishes the proposal.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from typing import Any, Dict

from pipeline.automod import deploy as _deploy

logger = logging.getLogger("jarvis.automod.ondemand")


def _spawn_live() -> bool:
    return os.environ.get("JARVIS_AUTOMOD_SPAWN_LIVE", "0") == "1"


def _autopublish() -> bool:
    return os.environ.get("JARVIS_EVOLUTION_AUTOPUBLISH", "0") == "1"


def run(intent_id: str) -> Dict[str, Any]:
    """Process one queued automod intent. Never raises."""
    summary: Dict[str, Any] = {
        "id": intent_id,
        "spawned": 0,
        "published": 0,
    }
    if not intent_id:
        return {**summary, "skipped": "missing-id"}
    if _deploy.read_marker():
        return {**summary, "skipped": "deploy-in-flight"}
    if not _spawn_live():
        return {**summary, "skipped": "spawn-disabled"}

    try:
        from pipeline.automod.spawner import drain_queue
        # Explicit, user-clicked build → uncapped (force) per the no-build-limit ask.
        summary["spawned"] = asyncio.run(drain_queue(only_id=intent_id, force=True))
    except Exception as e:  # noqa: BLE001
        logger.warning("[ondemand] spawn failed for %s: %s", intent_id, e)
        summary["spawn_error"] = str(e)
        return summary

    # Autonomy graduation (sub-project D) — TRIPLE-GATED, default no-op. Only
    # acts if stage is past human_review AND JARVIS_EVOLUTION_AUTODEPLOY=1.
    if summary["spawned"]:
        try:
            from pipeline.automod import graduation
            summary["auto_deploy"] = graduation.maybe_auto_deploy(intent_id)
        except Exception as e:  # noqa: BLE001
            summary["auto_deploy"] = {"action": "error", "reason": str(e)}

    if summary["spawned"] and _autopublish():
        try:
            from pipeline.automod import publish
            ok, info = publish.publish(intent_id)
            if ok:
                summary["published"] = 1
                summary["pr_url"] = info
            else:
                summary["publish_error"] = info
        except Exception as e:  # noqa: BLE001
            logger.warning("[ondemand] publish failed for %s: %s", intent_id, e)
            summary["publish_error"] = str(e)
    return summary


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    intent_id = argv[0] if argv else ""
    print(json.dumps(run(intent_id)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
