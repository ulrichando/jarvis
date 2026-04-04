"""Computer use lock management.

Ensures only one session at a time uses computer use features.
Uses O_EXCL atomic create, PID liveness probe, stale-lock recovery.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional, Union

logger = logging.getLogger(__name__)

LOCK_FILENAME = "computer-use.lock"


@dataclass
class ComputerUseLock:
    session_id: str
    pid: int
    acquired_at: int


@dataclass
class AcquiredResult:
    kind: Literal["acquired"] = "acquired"
    fresh: bool = True


@dataclass
class BlockedResult:
    kind: Literal["blocked"] = "blocked"
    by: str = ""


@dataclass
class FreeResult:
    kind: Literal["free"] = "free"


@dataclass
class HeldBySelfResult:
    kind: Literal["held_by_self"] = "held_by_self"


AcquireResult = Union[AcquiredResult, BlockedResult]
CheckResult = Union[FreeResult, HeldBySelfResult, BlockedResult]


def _get_lock_path() -> str:
    home = str(Path.home())
    config_dir = os.environ.get("JARVIS_HOME", os.path.join(home, ".jarvis"))
    return os.path.join(config_dir, LOCK_FILENAME)


def _is_process_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


async def acquire_computer_use_lock(session_id: str) -> AcquireResult:
    """Try to acquire the computer use lock."""
    lock_path = _get_lock_path()

    # Check for existing lock
    try:
        with open(lock_path) as f:
            data = json.load(f)
        existing = ComputerUseLock(**data)

        if existing.session_id == session_id:
            return AcquiredResult(fresh=False)

        if _is_process_running(existing.pid):
            return BlockedResult(by=existing.session_id)

        # Stale lock
        os.unlink(lock_path)
    except (FileNotFoundError, json.JSONDecodeError, TypeError):
        pass

    # Write new lock
    try:
        import time
        lock = ComputerUseLock(
            session_id=session_id,
            pid=os.getpid(),
            acquired_at=int(time.time() * 1000),
        )
        os.makedirs(os.path.dirname(lock_path), exist_ok=True)
        fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(lock.__dict__, f)
        return AcquiredResult(fresh=True)
    except FileExistsError:
        return BlockedResult(by="unknown")


async def release_computer_use_lock(session_id: str) -> None:
    """Release the computer use lock if we own it."""
    lock_path = _get_lock_path()
    try:
        with open(lock_path) as f:
            data = json.load(f)
        if data.get("session_id") == session_id:
            os.unlink(lock_path)
    except (FileNotFoundError, json.JSONDecodeError):
        pass


async def check_computer_use_lock(session_id: str) -> CheckResult:
    """Check the current state of the computer use lock."""
    lock_path = _get_lock_path()
    try:
        with open(lock_path) as f:
            data = json.load(f)
        existing = ComputerUseLock(**data)

        if existing.session_id == session_id:
            return HeldBySelfResult()

        if _is_process_running(existing.pid):
            return BlockedResult(by=existing.session_id)

        return FreeResult()
    except (FileNotFoundError, json.JSONDecodeError, TypeError):
        return FreeResult()
