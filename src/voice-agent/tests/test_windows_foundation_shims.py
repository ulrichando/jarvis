"""Cross-platform foundation shims — pipeline.portable_lock + pipeline.notify.

These replaced the Linux-only ``import fcntl`` / ``import sdnotify`` that
hard-ImportError'd the voice-agent on Windows (the Phase-2 boot blockers). The
tests cover the Linux path (which must stay byte-identical to the old
fcntl.flock behaviour) plus the platform-dispatch contract that makes Windows
boot safe. The actual Windows backends (msvcrt / no-op) are validated on a
Windows host; here we verify the Linux behaviour + the non-Linux dispatch that
doesn't touch fcntl/sdnotify.
"""
from __future__ import annotations

import platform

import pytest

from pipeline import notify, portable_lock


# ── portable_lock (replaces fcntl.flock) ─────────────────────────────────


def test_lock_acquire_and_release(tmp_path):
    p = tmp_path / "x.lock"
    with open(p, "a+", encoding="utf-8") as f:
        assert portable_lock.lock_exclusive(f) is True
        portable_lock.unlock(f)  # must not raise


def test_nonblocking_lock_reports_contention(tmp_path):
    """A second open-file-description can't take the lock while the first holds
    it (blocking=False → False), and can once it's released. This is exactly
    the cron_scheduler tick-overlap guard's semantics."""
    p = tmp_path / "x.lock"
    f1 = open(p, "a+", encoding="utf-8")
    f2 = open(p, "a+", encoding="utf-8")
    try:
        assert portable_lock.lock_exclusive(f1) is True
        assert portable_lock.lock_exclusive(f2, blocking=False) is False
        portable_lock.unlock(f1)
        assert portable_lock.lock_exclusive(f2, blocking=False) is True
        portable_lock.unlock(f2)
    finally:
        f1.close()
        f2.close()


def test_exclusive_lock_context_manager(tmp_path):
    p = tmp_path / "x.lock"
    with open(p, "a+", encoding="utf-8") as f:
        with portable_lock.exclusive_lock(f) as acquired:
            assert acquired is True
    # lock released on exit — a fresh handle can re-acquire non-blocking.
    with open(p, "a+", encoding="utf-8") as f2:
        assert portable_lock.lock_exclusive(f2, blocking=False) is True
        portable_lock.unlock(f2)


# ── notify (replaces direct sdnotify.SystemdNotifier) ────────────────────


def test_notify_get_notifier_callable_on_this_host():
    """get_notifier() always returns a .notify(state) object that never raises
    (real sdnotify on Linux — no-op when $NOTIFY_SOCKET is unset, as in tests)."""
    n = notify.get_notifier()
    n.notify("READY=1")
    n.notify("WATCHDOG=1")
    n.notify("STOPPING=1")


def test_notify_non_linux_is_noop_and_skips_sdnotify(monkeypatch):
    """The property that unblocks Windows boot: off Linux, get_notifier()
    returns a no-op WITHOUT importing sdnotify (systemd-only)."""
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    n = notify.get_notifier()
    assert type(n).__name__ == "_NoopNotifier"
    assert n.notify("WATCHDOG=1") is None  # accepts + drops, never raises
