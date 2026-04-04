"""Scheduler lease lock for scheduled tasks.

Ensures only one session drives the cron scheduler per project.
Uses O_EXCL atomic create, PID liveness probe, stale-lock recovery.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

LOCK_FILE_REL = os.path.join(".jarvis", "scheduled_tasks.lock")


@dataclass
class SchedulerLock:
    session_id: str
    pid: int
    acquired_at: int


@dataclass
class SchedulerLockOptions:
    directory: Optional[str] = None
    lock_identity: Optional[str] = None


_unregister_cleanup = None
_last_blocked_by: Optional[str] = None


def _get_lock_path(directory: Optional[str] = None) -> str:
    if directory is None:
        directory = os.getcwd()
    return os.path.join(directory, LOCK_FILE_REL)


def _is_process_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


async def try_acquire_scheduler_lock(
    session_id: str, opts: Optional[SchedulerLockOptions] = None
) -> bool:
    """Try to acquire the scheduler lock. Returns True on success."""
    global _last_blocked_by
    directory = opts.directory if opts else None
    identity = (opts.lock_identity if opts else None) or session_id
    lock_path = _get_lock_path(directory)

    import time
    lock_data = SchedulerLock(
        session_id=identity,
        pid=os.getpid(),
        acquired_at=int(time.time() * 1000),
    )

    # Try exclusive create
    try:
        os.makedirs(os.path.dirname(lock_path), exist_ok=True)
        fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        with os.fdopen(fd, "w") as f:
            json.dump(lock_data.__dict__, f)
        _last_blocked_by = None
        logger.debug(f"[ScheduledTasks] acquired scheduler lock (PID {os.getpid()})")
        return True
    except FileExistsError:
        pass

    # Check existing lock
    try:
        with open(lock_path) as f:
            existing = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return False

    # Already ours
    if existing.get("session_id") == identity or existing.get("sessionId") == identity:
        return True

    # Another live session
    existing_pid = existing.get("pid", 0)
    if _is_process_running(existing_pid):
        return False

    # Stale - remove and retry
    try:
        os.unlink(lock_path)
        fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        with os.fdopen(fd, "w") as f:
            json.dump(lock_data.__dict__, f)
        return True
    except (FileExistsError, OSError):
        return False


async def release_scheduler_lock(
    session_id: str = "", opts: Optional[SchedulerLockOptions] = None
) -> None:
    """Release the scheduler lock if we own it."""
    directory = opts.directory if opts else None
    identity = (opts.lock_identity if opts else None) or session_id
    lock_path = _get_lock_path(directory)

    try:
        with open(lock_path) as f:
            existing = json.load(f)
        sid = existing.get("session_id") or existing.get("sessionId")
        if sid != identity:
            return
        os.unlink(lock_path)
        logger.debug("[ScheduledTasks] released scheduler lock")
    except (FileNotFoundError, json.JSONDecodeError):
        pass
