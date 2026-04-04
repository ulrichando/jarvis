"""
File locking utilities using fcntl for Unix systems.
"""

from __future__ import annotations

import fcntl
import os
from typing import Optional


class FileLock:
    """Simple file-based lock using fcntl."""

    def __init__(self, path: str) -> None:
        self._path = path + ".lock"
        self._fd: Optional[int] = None

    def acquire(self, blocking: bool = True) -> bool:
        """
        Acquire the lock.

        Args:
            blocking: If True, block until the lock is acquired.

        Returns:
            True if lock was acquired, False otherwise.
        """
        try:
            self._fd = os.open(self._path, os.O_CREAT | os.O_WRONLY)
            flags = fcntl.LOCK_EX
            if not blocking:
                flags |= fcntl.LOCK_NB
            fcntl.flock(self._fd, flags)
            return True
        except (OSError, IOError):
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None
            return False

    def release(self) -> None:
        """Release the lock."""
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
                os.close(self._fd)
            except (OSError, IOError):
                pass
            self._fd = None
            try:
                os.unlink(self._path)
            except OSError:
                pass


def lock(file: str, retries: int = 3, realpath: bool = True) -> FileLock:
    """
    Lock a file.

    Args:
        file: Path to the file to lock.
        retries: Number of retry attempts.
        realpath: Whether to resolve the real path.

    Returns:
        FileLock instance (already acquired).
    """
    fl = FileLock(file)
    blocking = retries > 0
    if not fl.acquire(blocking=blocking):
        raise OSError(f"Could not acquire lock on {file}")
    return fl


def unlock(file: str, realpath: bool = True) -> None:
    """
    Unlock a file (remove lockfile).

    Args:
        file: Path to the file to unlock.
        realpath: Whether to resolve the real path.
    """
    lock_path = file + ".lock"
    try:
        os.unlink(lock_path)
    except OSError:
        pass
