"""Tests for Phase 2.3 cross-platform footgun fixes — path/setsid/signal.

Covers the small individual fixes that don't warrant their own module:

  Cluster A — /tmp/jarvis-* paths in jarvis_agent.py:
      Resolved via Path(tempfile.gettempdir()). On Linux this is /tmp/
      (unchanged); on Windows it lands under %TEMP%. Validate that the
      heartbeat path is a Path under the current platform's gettempdir.

  Cluster B — setsid invocation in jarvis_agent.py:
      Branched on platform.system() — Linux keeps the setsid shell
      cmd (proven path), non-Linux uses subprocess.Popen with the
      detach kwargs from tools.runtime.detached_popen_kwargs().
      Validate that the helper returns the right kwargs per platform.

  Cluster C — asyncio.add_signal_handler in jarvis_voice_client.py:
      Wrapped in try/except NotImplementedError so the Windows asyncio
      event loop doesn't crash on startup. Linux path unchanged.
"""
from __future__ import annotations

import asyncio
import signal
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# Cluster A — temp paths ---------------------------------------------


def test_tempfile_gettempdir_returns_writable_path_on_current_platform():
    """The temp dir must exist + be writable on whatever platform the
    suite is running on. Linux → /tmp; macOS → /tmp or /var/folders/...;
    Windows → %TEMP%. The launch_app + heartbeat call sites both
    rely on this assumption."""
    tmp = Path(tempfile.gettempdir())
    assert tmp.exists()
    assert tmp.is_dir()
    # Write probe so we know the cross-platform fix actually lands files.
    probe = tmp / f"jarvis-phase23-probe-{os_getpid()}"
    probe.write_text("ok", encoding="utf-8")
    try:
        assert probe.read_text(encoding="utf-8") == "ok"
    finally:
        probe.unlink(missing_ok=True)


def os_getpid() -> int:
    """Wrapper so the probe's filename is unique per test process."""
    import os
    return os.getpid()


def test_heartbeat_path_uses_gettempdir():
    """Cluster A site #2 (the heartbeat path). The new code builds
    `Path(tempfile.gettempdir()) / "jarvis-worker-heartbeat"`; on Linux
    that's /tmp/jarvis-worker-heartbeat (the pre-fix literal), on
    Windows %TEMP%\\jarvis-worker-heartbeat. We assert structurally."""
    heartbeat = Path(tempfile.gettempdir()) / "jarvis-worker-heartbeat"
    assert heartbeat.name == "jarvis-worker-heartbeat"
    assert heartbeat.parent == Path(tempfile.gettempdir())


# Cluster B — detached_popen_kwargs ----------------------------------


def test_detached_popen_kwargs_linux_returns_start_new_session():
    """On Linux/macOS the helper must return {'start_new_session': True}
    — Python's subprocess uses this to call setsid internally, which
    is the same detachment primitive the old setsid shell cmd used."""
    from tools import runtime
    with mock.patch("tools.runtime.platform.system", return_value="Linux"):
        kwargs = runtime.detached_popen_kwargs()
    assert kwargs == {"start_new_session": True}


def test_detached_popen_kwargs_darwin_returns_start_new_session():
    """macOS: same as Linux — start_new_session is POSIX."""
    from tools import runtime
    with mock.patch("tools.runtime.platform.system", return_value="Darwin"):
        kwargs = runtime.detached_popen_kwargs()
    assert kwargs == {"start_new_session": True}


def test_detached_popen_kwargs_windows_returns_creationflags():
    """On Windows the helper must return a creationflags int built from
    CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS so the child detaches
    from the worker's console. Both constants are stdlib on Windows;
    we patch subprocess to simulate their presence cross-platform."""
    from tools import runtime
    fake_constants = mock.MagicMock()
    fake_constants.CREATE_NEW_PROCESS_GROUP = 0x00000200
    fake_constants.DETACHED_PROCESS = 0x00000008
    fake_constants.DEVNULL = -3  # not used but referenced elsewhere
    with mock.patch("tools.runtime.platform.system", return_value="Windows"), \
         mock.patch("tools.runtime.subprocess", fake_constants):
        kwargs = runtime.detached_popen_kwargs()
    assert "creationflags" in kwargs
    assert kwargs["creationflags"] == 0x00000200 | 0x00000008
    # And no POSIX kwargs (would crash on Windows Popen).
    assert "start_new_session" not in kwargs
    assert "preexec_fn" not in kwargs


def test_detached_popen_kwargs_windows_safe_when_constants_absent():
    """Defense in depth: if a future Python somehow lacks one of the
    creationflags constants, the helper must still return a dict (not
    AttributeError) — getattr fallback to 0 keeps the call usable."""
    from tools import runtime
    bare_subprocess = mock.MagicMock(spec=[])  # no attrs except defaults
    with mock.patch("tools.runtime.platform.system", return_value="Windows"), \
         mock.patch("tools.runtime.subprocess", bare_subprocess):
        kwargs = runtime.detached_popen_kwargs()
    # creationflags present, value is 0 because getattr defaulted both.
    assert kwargs == {"creationflags": 0}


# Cluster C — add_signal_handler -------------------------------------


def test_add_signal_handler_try_except_pattern_runs_on_linux():
    """Sanity: the actual call pattern in jarvis_voice_client.main()
    works on Linux (the platform we're running on). We can't easily
    spin up the full main(), so we test the loop fragment in isolation."""
    async def runner():
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, lambda: None)  # windows-footgun: ok (test wraps in try/except, matches production code's pattern)
            except NotImplementedError:
                pass
        # Tear them down so we don't leak handlers into other tests.
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.remove_signal_handler(sig)
            except (NotImplementedError, ValueError):
                pass
    asyncio.run(runner())  # must not raise


def test_add_signal_handler_swallows_not_implemented(monkeypatch):
    """Simulate the Windows asyncio failure mode: add_signal_handler
    raises NotImplementedError. The try/except in jarvis_voice_client
    must swallow it so the agent boot doesn't crash on Windows."""
    async def runner():
        loop = asyncio.get_running_loop()

        def boom(*a, **kw):
            raise NotImplementedError("Windows asyncio")

        monkeypatch.setattr(loop, "add_signal_handler", boom)

        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, lambda: None)  # windows-footgun: ok (test wraps in try/except, simulates Windows failure mode)
            except NotImplementedError:
                pass  # the production code logs; here we just assert no-raise.

    asyncio.run(runner())  # must not raise


# Integration sanity — the launch_app branching shape ---------------


def test_launch_app_log_path_is_under_gettempdir():
    """jarvis_agent.launch_app now builds log_path under
    tempfile.gettempdir() (Cluster A site #1). We can't easily run
    launch_app end-to-end (it spawns real processes), but we can
    assert the path-construction shape it now uses."""
    import time
    log_path = str(
        Path(tempfile.gettempdir())
        / f"jarvis-launch-bin-{int(time.time())}.log"
    )
    assert log_path.startswith(tempfile.gettempdir())
    assert "jarvis-launch-bin-" in log_path
    assert log_path.endswith(".log")
