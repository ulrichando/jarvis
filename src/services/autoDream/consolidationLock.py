"""
Consolidation lock management.

Lock file whose mtime IS lastConsolidatedAt. Body is the holder's PID.
Lives inside the memory dir so it keys on git-root like memory does.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

LOCK_FILE = ".consolidate-lock"
HOLDER_STALE_MS = 60 * 60 * 1000  # 1 hour


def _lock_path() -> Path:
    """Get the path to the consolidation lock file."""
    mem_dir = os.environ.get("JARVIS_MEMORY_DIR", os.path.expanduser("~/.jarvis/memory"))
    return Path(mem_dir) / LOCK_FILE


def _is_process_running(pid: int) -> bool:
    """Check if a process with the given PID is running."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


async def read_last_consolidated_at() -> float:
    """Read the mtime of the lock file (= lastConsolidatedAt). 0 if absent."""
    try:
        path = _lock_path()
        if path.exists():
            return path.stat().st_mtime * 1000  # Convert to ms
        return 0.0
    except Exception:
        return 0.0


async def try_acquire_consolidation_lock() -> Optional[float]:
    """Acquire the consolidation lock.

    Returns the pre-acquire mtime (for rollback), or None if blocked.
    """
    path = _lock_path()
    mtime_ms: Optional[float] = None
    holder_pid: Optional[int] = None

    try:
        if path.exists():
            mtime_ms = path.stat().st_mtime * 1000
            content = path.read_text().strip()
            try:
                holder_pid = int(content)
            except ValueError:
                holder_pid = None
    except Exception:
        pass

    if mtime_ms is not None and (time.time() * 1000 - mtime_ms) < HOLDER_STALE_MS:
        if holder_pid is not None and _is_process_running(holder_pid):
            logger.debug(
                f"[autoDream] lock held by live PID {holder_pid} "
                f"(mtime {int((time.time() * 1000 - mtime_ms) / 1000)}s ago)"
            )
            return None

    # Acquire: write PID
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(os.getpid()))

    # Verify we won the race
    try:
        verify = path.read_text().strip()
        if int(verify) != os.getpid():
            return None
    except Exception:
        return None

    return mtime_ms if mtime_ms is not None else 0.0


async def rollback_consolidation_lock(prior_mtime: float) -> None:
    """Rewind mtime to pre-acquire after a failed consolidation."""
    path = _lock_path()
    try:
        if prior_mtime == 0:
            path.unlink(missing_ok=True)
            return
        path.write_text("")
        t = prior_mtime / 1000  # utimes wants seconds
        os.utime(path, (t, t))
    except Exception as e:
        logger.debug(f"[autoDream] rollback failed: {e}")


async def record_consolidation() -> None:
    """Stamp from manual /dream command."""
    try:
        path = _lock_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(os.getpid()))
    except Exception as e:
        logger.debug(f"[autoDream] recordConsolidation write failed: {e}")
