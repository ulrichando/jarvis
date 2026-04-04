"""
Agent memory snapshot management.

Handles syncing agent memory between project snapshots and local storage.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

from src.tools.AgentTool.agentMemory import AgentMemoryScope, get_agent_memory_dir

logger = logging.getLogger(__name__)

SNAPSHOT_BASE = "agent-memory-snapshots"
SNAPSHOT_JSON = "snapshot.json"
SYNCED_JSON = ".snapshot-synced.json"


def get_snapshot_dir_for_agent(agent_type: str, cwd: Optional[str] = None) -> str:
    """Returns the path to the snapshot directory for an agent in the current project."""
    base = cwd or os.getcwd()
    return os.path.join(base, ".claude", SNAPSHOT_BASE, agent_type)


def _get_snapshot_json_path(agent_type: str) -> str:
    return os.path.join(get_snapshot_dir_for_agent(agent_type), SNAPSHOT_JSON)


def _get_synced_json_path(agent_type: str, scope: AgentMemoryScope) -> str:
    return os.path.join(get_agent_memory_dir(agent_type, scope), SYNCED_JSON)


def _read_json_file(path: str) -> Optional[dict]:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


async def _copy_snapshot_to_local(
    agent_type: str,
    scope: AgentMemoryScope,
) -> None:
    snapshot_mem_dir = get_snapshot_dir_for_agent(agent_type)
    local_mem_dir = get_agent_memory_dir(agent_type, scope)

    os.makedirs(local_mem_dir, exist_ok=True)

    try:
        for entry in os.scandir(snapshot_mem_dir):
            if entry.is_file() and entry.name != SNAPSHOT_JSON:
                shutil.copy2(entry.path, os.path.join(local_mem_dir, entry.name))
    except OSError as e:
        logger.debug(f"Failed to copy snapshot to local agent memory: {e}")


async def _save_synced_meta(
    agent_type: str,
    scope: AgentMemoryScope,
    snapshot_timestamp: str,
) -> None:
    synced_path = _get_synced_json_path(agent_type, scope)
    local_mem_dir = get_agent_memory_dir(agent_type, scope)
    os.makedirs(local_mem_dir, exist_ok=True)
    meta = {"syncedFrom": snapshot_timestamp}
    try:
        with open(synced_path, "w", encoding="utf-8") as f:
            json.dump(meta, f)
    except OSError as e:
        logger.debug(f"Failed to save snapshot sync metadata: {e}")


@dataclass
class SnapshotCheckResult:
    action: Literal["none", "initialize", "prompt-update"]
    snapshot_timestamp: Optional[str] = None


async def check_agent_memory_snapshot(
    agent_type: str,
    scope: AgentMemoryScope,
) -> SnapshotCheckResult:
    """Check if a snapshot exists and whether it's newer than what we last synced."""
    snapshot_meta = _read_json_file(_get_snapshot_json_path(agent_type))

    if not snapshot_meta or "updatedAt" not in snapshot_meta:
        return SnapshotCheckResult(action="none")

    local_mem_dir = get_agent_memory_dir(agent_type, scope)

    has_local_memory = False
    try:
        for entry in os.scandir(local_mem_dir):
            if entry.is_file() and entry.name.endswith(".md"):
                has_local_memory = True
                break
    except OSError:
        pass

    if not has_local_memory:
        return SnapshotCheckResult(
            action="initialize",
            snapshot_timestamp=snapshot_meta["updatedAt"],
        )

    synced_meta = _read_json_file(_get_synced_json_path(agent_type, scope))

    if not synced_meta or "syncedFrom" not in synced_meta:
        return SnapshotCheckResult(
            action="prompt-update",
            snapshot_timestamp=snapshot_meta["updatedAt"],
        )

    if snapshot_meta["updatedAt"] > synced_meta["syncedFrom"]:
        return SnapshotCheckResult(
            action="prompt-update",
            snapshot_timestamp=snapshot_meta["updatedAt"],
        )

    return SnapshotCheckResult(action="none")


async def initialize_from_snapshot(
    agent_type: str,
    scope: AgentMemoryScope,
    snapshot_timestamp: str,
) -> None:
    """Initialize local agent memory from a snapshot (first-time setup)."""
    logger.debug(f"Initializing agent memory for {agent_type} from project snapshot")
    await _copy_snapshot_to_local(agent_type, scope)
    await _save_synced_meta(agent_type, scope, snapshot_timestamp)


async def replace_from_snapshot(
    agent_type: str,
    scope: AgentMemoryScope,
    snapshot_timestamp: str,
) -> None:
    """Replace local agent memory with the snapshot."""
    logger.debug(f"Replacing agent memory for {agent_type} with project snapshot")
    local_mem_dir = get_agent_memory_dir(agent_type, scope)
    try:
        for entry in os.scandir(local_mem_dir):
            if entry.is_file() and entry.name.endswith(".md"):
                os.unlink(entry.path)
    except OSError:
        pass
    await _copy_snapshot_to_local(agent_type, scope)
    await _save_synced_meta(agent_type, scope, snapshot_timestamp)


async def mark_snapshot_synced(
    agent_type: str,
    scope: AgentMemoryScope,
    snapshot_timestamp: str,
) -> None:
    """Mark the current snapshot as synced without changing local memory."""
    await _save_synced_meta(agent_type, scope, snapshot_timestamp)
