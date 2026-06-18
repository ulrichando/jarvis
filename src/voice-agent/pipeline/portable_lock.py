"""Cross-platform advisory exclusive file lock.

JARVIS serializes a handful of concurrent writers (the curated memory files,
the cron tick, skill-usage counters, the auto-mod spawn) with an advisory
exclusive lock on an open file. The Linux path was bare ``fcntl.flock`` —
which **hard-``ImportError``s on Windows** (``fcntl`` is a Unix-only stdlib
module). Importing it at module scope (as ``cron_delivery``/``cron_scheduler``
do, and those are pulled in at agent startup) means the whole voice-agent
fails to import on Windows before serving a single turn.

This module is the platform-dispatched replacement. The public surface mirrors
the ``flock`` call shapes it replaces, so the swap at each site is mechanical:

    # before
    fcntl.flock(f, fcntl.LOCK_EX)            →  lock_exclusive(f)
    fcntl.flock(f, fcntl.LOCK_EX|LOCK_NB)    →  lock_exclusive(f, blocking=False)
    fcntl.flock(f, fcntl.LOCK_UN)            →  unlock(f)
    # or the context-manager form for new code:
    with exclusive_lock(f):                  ...

Backends
--------
POSIX
    ``fcntl.flock`` — whole-file advisory lock. **Byte-for-byte the same
    behaviour** as the call sites had before; the test suite's existing
    locking coverage exercises this path unchanged.
Windows
    ``msvcrt.locking`` — locks a 1-byte region at offset 0 of the open fd
    (Windows permits locking a region beyond EOF, so empty lock files are
    fine). ``LK_LOCK`` blocks-with-retry (~10 s) then raises; ``LK_NBLCK``
    fails immediately. JARVIS's locks are held for milliseconds, so the
    bounded blocking is not a practical regression.

This deliberately follows the *intent* of Claude Code's cross-platform
``proper-lockfile`` (a mkdir/atomic-dir lock, not ``fcntl``) — but stays an
fd-level advisory lock to keep the call-site diff minimal. If stale-lock
robustness is ever needed, swap the impl here for the ``filelock`` package
(pure-Python, universal wheels) WITHOUT touching the call sites.
"""
from __future__ import annotations

import sys
from contextlib import contextmanager
from typing import IO, Iterator

_IS_WINDOWS = sys.platform == "win32"

if _IS_WINDOWS:  # pragma: no cover - exercised on Windows only
    import msvcrt
else:
    import fcntl  # windows-footgun: ok (POSIX backend of this portable-lock shim)


def lock_exclusive(fileobj: IO, *, blocking: bool = True) -> bool:
    """Acquire an exclusive advisory lock on an open file object.

    Returns ``True`` when the lock is held on return. With ``blocking=False``
    and the lock already held by another process, returns ``False`` instead of
    blocking (mirrors ``LOCK_EX | LOCK_NB``). Re-raises unexpected OS errors.
    """
    fd = fileobj.fileno()
    if _IS_WINDOWS:  # pragma: no cover - Windows only
        mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
        try:
            fileobj.seek(0)
        except (OSError, ValueError):
            pass
        try:
            msvcrt.locking(fd, mode, 1)
            return True
        except OSError:
            if not blocking:
                return False
            raise
    flags = fcntl.LOCK_EX if blocking else (fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        fcntl.flock(fd, flags)
        return True
    except (BlockingIOError, OSError):
        if not blocking:
            return False
        raise


def unlock(fileobj: IO) -> None:
    """Release a lock previously taken with :func:`lock_exclusive`. Never raises."""
    fd = fileobj.fileno()
    if _IS_WINDOWS:  # pragma: no cover - Windows only
        try:
            fileobj.seek(0)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except (OSError, ValueError):
            pass
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass


@contextmanager
def exclusive_lock(fileobj: IO, *, blocking: bool = True) -> Iterator[bool]:
    """Context manager: lock on enter, unlock on exit.

    Yields ``True`` if the lock was acquired, ``False`` if ``blocking=False``
    and it was contended (the body still runs — check the yielded value).
    """
    acquired = lock_exclusive(fileobj, blocking=blocking)
    try:
        yield acquired
    finally:
        if acquired:
            unlock(fileobj)
