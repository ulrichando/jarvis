"""LoopWatchdog._dump_stall_diagnostics: dumps thread stacks + asyncio
tasks before the watchdog kills the process.

The diagnostic complements `slow_callback_duration` for the case where
a callback NEVER finishes (blocking I/O, GIL-held C extension,
sync-over-async). Live-observed 2026-05-05: three voice-client stalls
(09:56, 10:26, 11:15 EDT) emitted zero slow-callback warnings — proving
the slow-running diagnostic alone is insufficient.

Refactored 2026-05-10 (Step 7 of the audit): watchdog moved from
module-level globals to a `LoopWatchdog` class in
`voice_client_watchdog`. Tests now construct a watchdog instance with
stub deps and call methods on it.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import voice_client_watchdog as wd


@pytest.fixture
def capture_log(caplog):
    caplog.set_level(logging.ERROR, logger="jarvis.voice_client")
    return caplog


def _make_watchdog() -> wd.LoopWatchdog:
    """Construct a LoopWatchdog with stub state + the real
    `jarvis.voice_client` logger (so caplog at that logger sees the
    dump output)."""
    log = logging.getLogger("jarvis.voice_client")
    state = MagicMock()
    state.connected = False
    state.agent_present = False
    state.listening = False
    state.speaking = False
    state.tool_running = False
    state.agent_thinking = False
    return wd.LoopWatchdog(
        state=state,
        log=log,
        restart_agent_unit=lambda: None,
    )


def test_dump_stall_diagnostics_logs_thread_stacks_and_age(capture_log):
    """The diagnostic must always log the stall age + a thread-dump
    header, even when no asyncio main loop has been captured yet.
    Best-effort: failures inside the dump are swallowed so the kill
    path stays clean."""
    watchdog = _make_watchdog()
    # _main_loop intentionally None — we never called start_os_thread.
    watchdog._dump_stall_diagnostics(72.5)

    text = capture_log.text
    assert "STALL 72s OLD" in text or "STALL 73s OLD" in text, (
        "expected stall-age banner in error log, got: " + text[-500:]
    )
    assert "all-thread tracebacks" in text
    assert "_main_loop unset" in text, (
        "expected explicit unset-loop note when _main_loop is None"
    )


def test_dump_stall_diagnostics_lists_asyncio_tasks(capture_log):
    """When _main_loop is captured (the production path), the diagnostic
    must enumerate live asyncio tasks. Spawn a dummy task and verify it
    appears in the dump."""
    async def _setup_and_dump() -> None:
        loop = asyncio.get_running_loop()
        watchdog = _make_watchdog()
        watchdog._main_loop = loop

        async def _idle() -> None:
            await asyncio.sleep(60)  # Won't actually wait — we cancel below.

        task = asyncio.create_task(_idle(), name="diag-test-task")
        try:
            # Yield once so the task actually starts and is "live" in
            # all_tasks().
            await asyncio.sleep(0)
            watchdog._dump_stall_diagnostics(65.0)
        finally:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, BaseException):
                pass

    asyncio.run(_setup_and_dump())

    text = capture_log.text
    assert "asyncio task(s) on main loop" in text, (
        "expected task-count line, got: " + text[-500:]
    )
    assert "diag-test-task" in text, (
        "expected the test task by name in the dump"
    )


def test_dump_stall_diagnostics_swallows_internal_errors(capture_log, monkeypatch):
    """If something inside the dump raises, the function must not
    propagate — it's called from the watchdog OS thread immediately
    before os._exit(1) and any propagation would block the kill."""
    def _boom(*args, **kw):
        raise RuntimeError("simulated faulthandler failure")

    monkeypatch.setattr(wd.faulthandler, "dump_traceback", _boom)
    watchdog = _make_watchdog()
    # Must NOT raise.
    watchdog._dump_stall_diagnostics(99.0)

    assert "faulthandler dump failed" in capture_log.text, (
        "expected the swallowed-error log line"
    )
