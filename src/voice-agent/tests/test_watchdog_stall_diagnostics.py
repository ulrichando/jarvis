"""_dump_stall_diagnostics: dumps thread stacks + asyncio tasks before
the watchdog kills the process.

The diagnostic complements `slow_callback_duration` for the case where
a callback NEVER finishes (blocking I/O, GIL-held C extension,
sync-over-async). Live-observed 2026-05-05: three voice-client stalls
(09:56, 10:26, 11:15 EDT) emitted zero slow-callback warnings — proving
the slow-running diagnostic alone is insufficient.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import jarvis_voice_client as vc


@pytest.fixture
def capture_log(caplog):
    caplog.set_level(logging.ERROR, logger="voice-client")
    return caplog


def test_dump_stall_diagnostics_logs_thread_stacks_and_age(capture_log):
    """The diagnostic must always log the stall age + a thread-dump
    header, even when no asyncio main loop has been captured yet.
    Best-effort: failures inside the dump are swallowed so the kill
    path stays clean."""
    saved_loop = vc._main_loop
    vc._main_loop = None
    try:
        vc._dump_stall_diagnostics(72.5)
    finally:
        vc._main_loop = saved_loop

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
    appears in the dump.
    """
    async def _setup_and_dump() -> None:
        loop = asyncio.get_running_loop()
        saved_loop = vc._main_loop
        vc._main_loop = loop

        async def _idle() -> None:
            await asyncio.sleep(60)  # Won't actually wait — we cancel below.

        task = asyncio.create_task(_idle(), name="diag-test-task")
        try:
            # Yield once so the task actually starts and is "live" in
            # all_tasks().
            await asyncio.sleep(0)
            vc._dump_stall_diagnostics(65.0)
        finally:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, BaseException):
                pass
            vc._main_loop = saved_loop

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

    monkeypatch.setattr(vc.faulthandler, "dump_traceback", _boom)
    saved_loop = vc._main_loop
    vc._main_loop = None
    try:
        # Must NOT raise.
        vc._dump_stall_diagnostics(99.0)
    finally:
        vc._main_loop = saved_loop

    assert "faulthandler dump failed" in capture_log.text, (
        "expected the swallowed-error log line"
    )
