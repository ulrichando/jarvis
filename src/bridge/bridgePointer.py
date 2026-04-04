"""Crash-recovery pointer for Remote Control sessions."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

BRIDGE_POINTER_TTL_MS = 4 * 60 * 60 * 1000
MAX_WORKTREE_FANOUT = 50


@dataclass
class BridgePointer:
    session_id: str
    environment_id: str
    source: str  # 'standalone' | 'repl'
    age_ms: int = 0


def _sanitize_path(dir_path: str) -> str:
    """Sanitize a directory path for use as a storage key."""
    return dir_path.replace("/", "_").replace("\\", "_").lstrip("_")


def _get_projects_dir() -> str:
    """Get the projects directory for session storage."""
    home = os.environ.get("JARVIS_HOME", os.path.expanduser("~/.jarvis"))
    return os.path.join(home, "projects")


def get_bridge_pointer_path(dir_path: str) -> str:
    """Get the path to the bridge pointer file for a directory."""
    return os.path.join(_get_projects_dir(), _sanitize_path(dir_path), "bridge-pointer.json")


async def write_bridge_pointer(dir_path: str, pointer: BridgePointer) -> None:
    """Write the pointer. Also used to refresh mtime during long sessions."""
    path = get_bridge_pointer_path(dir_path)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = {
            "sessionId": pointer.session_id,
            "environmentId": pointer.environment_id,
            "source": pointer.source,
        }
        with open(path, "w") as f:
            json.dump(data, f)
        logger.debug("[bridge:pointer] wrote %s", path)
    except Exception as err:
        logger.warning("[bridge:pointer] write failed: %s", err)


async def read_bridge_pointer(dir_path: str) -> Optional[BridgePointer]:
    """Read the pointer and its age. Returns None on any failure."""
    path = get_bridge_pointer_path(dir_path)
    try:
        stat = os.stat(path)
        mtime_ms = stat.st_mtime * 1000
        with open(path) as f:
            raw = f.read()
    except (FileNotFoundError, OSError):
        return None

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.debug("[bridge:pointer] invalid JSON, clearing: %s", path)
        await clear_bridge_pointer(dir_path)
        return None

    session_id = data.get("sessionId")
    environment_id = data.get("environmentId")
    source = data.get("source")
    if not session_id or not environment_id or source not in ("standalone", "repl"):
        logger.debug("[bridge:pointer] invalid schema, clearing: %s", path)
        await clear_bridge_pointer(dir_path)
        return None

    age_ms = max(0, int(time.time() * 1000 - mtime_ms))
    if age_ms > BRIDGE_POINTER_TTL_MS:
        logger.debug("[bridge:pointer] stale (>4h mtime), clearing: %s", path)
        await clear_bridge_pointer(dir_path)
        return None

    return BridgePointer(
        session_id=session_id,
        environment_id=environment_id,
        source=source,
        age_ms=age_ms,
    )


async def read_bridge_pointer_across_worktrees(
    dir_path: str,
) -> Optional[dict]:
    """Worktree-aware read for --continue."""
    here = await read_bridge_pointer(dir_path)
    if here:
        return {"pointer": here, "dir": dir_path}
    # In Python version, simplified -- no git worktree fanout
    return None


async def clear_bridge_pointer(dir_path: str) -> None:
    """Delete the pointer. Idempotent."""
    path = get_bridge_pointer_path(dir_path)
    try:
        os.unlink(path)
        logger.debug("[bridge:pointer] cleared %s", path)
    except FileNotFoundError:
        pass
    except Exception as err:
        logger.warning("[bridge:pointer] clear failed: %s", err)
