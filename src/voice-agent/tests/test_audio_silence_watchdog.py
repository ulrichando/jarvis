"""Tests for `resilience/audio_silence_watchdog.py`.

The watchdog tracks two clocks:
  - `_last_job_started_ts` — set by `mark_job_started()`
  - `_last_audio_activity_ts` — bumped by `mark_audio_activity()`

If silence (max(both)) exceeds the budget, sys.exit(1) fires.

These tests use a stub for `sys.exit` so the test process survives.
"""
from __future__ import annotations

import asyncio
import importlib

import pytest


@pytest.fixture
def asw(monkeypatch):
    """Fresh `audio_silence_watchdog` module per test — env vars are
    read at import time, so we reload with the test's values applied
    first."""
    # Tight timeouts so tests don't sit for 90s.
    monkeypatch.setenv("JARVIS_AUDIO_SILENCE_TIMEOUT_S", "0.5")
    monkeypatch.setenv("JARVIS_AUDIO_SILENCE_CHECK_INTERVAL_S", "0.05")
    from resilience import audio_silence_watchdog as mod
    importlib.reload(mod)
    # Stub sys.exit so the test survives. Raising SystemExit from
    # inside an asyncio task tears down the event loop in some
    # pytest configs, so we record the call instead. The watchdog
    # sets `_exit_called = True` BEFORE calling sys.exit, so tests
    # check that flag (not the exception).
    exit_calls = []
    def _fake_exit(code=0):
        exit_calls.append(code)
    monkeypatch.setattr(mod.sys, "exit", _fake_exit)
    mod._exit_calls = exit_calls  # expose to tests
    # Reset module state from prior test.
    mod._last_job_started_ts = 0.0
    mod._last_audio_activity_ts = 0.0
    mod._task = None
    mod._exit_called = False
    return mod


# ── basic state ────────────────────────────────────────────────────


def test_mark_job_started_resets_both_clocks(asw):
    asw.mark_job_started()
    assert asw._last_job_started_ts > 0
    assert asw._last_audio_activity_ts > 0


def test_mark_audio_activity_only_bumps_activity(asw):
    asw.mark_job_started()
    asw._last_job_started_ts = 100.0  # freeze for comparison
    asw.mark_audio_activity()
    assert asw._last_audio_activity_ts > 100.0
    assert asw._last_job_started_ts == 100.0


def test_is_running_false_before_start(asw):
    assert asw.is_running() is False


# ── loop behavior ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_job_started_never_fires_exit(asw):
    """Until mark_job_started fires, the watchdog has nothing to
    compare against and must NOT call sys.exit."""
    stop = asyncio.Event()
    task = asw.start_audio_silence_watchdog_task(stop=stop)
    assert task is not None
    # Let several poll intervals elapse.
    await asyncio.sleep(0.3)
    stop.set()
    await task
    assert not asw._exit_called


@pytest.mark.asyncio
async def test_activity_within_budget_does_not_fire(asw):
    """If we keep bumping activity, the watchdog stays armed but
    never trips."""
    stop = asyncio.Event()
    task = asw.start_audio_silence_watchdog_task(stop=stop)
    asw.mark_job_started()
    # Bump activity every ~50ms for ~400ms (well past the 500ms budget
    # if we DIDN'T bump). Total < budget anyway since we keep refreshing.
    for _ in range(8):
        await asyncio.sleep(0.05)
        asw.mark_audio_activity()
    stop.set()
    await task
    assert not asw._exit_called


@pytest.mark.asyncio
async def test_silence_past_budget_fires_exit(asw):
    """Job started + no activity → after the budget elapses,
    sys.exit(1) fires. The fake sys.exit raises a SystemExit-subclass
    that asyncio captures into the task's exception, so we check the
    `_exit_called` flag the loop sets BEFORE calling sys.exit."""
    task = asw.start_audio_silence_watchdog_task()
    asw.mark_job_started()
    # Poll until the watchdog flips `_exit_called` (set immediately
    # before the fake sys.exit). Bounded by a generous timeout.
    deadline = asyncio.get_event_loop().time() + 2.0
    while not asw._exit_called and asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.05)
    # Cancel the (still-pending or already-raised) task to avoid
    # pytest's "task exception was never retrieved" warning.
    if not task.done():
        task.cancel()
    try:
        await task
    except BaseException:
        pass
    assert asw._exit_called, "watchdog should have called sys.exit within 2s"


@pytest.mark.asyncio
async def test_start_twice_returns_same_task(asw):
    stop = asyncio.Event()
    a = asw.start_audio_silence_watchdog_task(stop=stop)
    b = asw.start_audio_silence_watchdog_task(stop=stop)
    assert a is b
    stop.set()
    await a


@pytest.mark.asyncio
async def test_disabled_via_env_returns_none(monkeypatch):
    """Setting JARVIS_AUDIO_SILENCE_TIMEOUT_S=0 disables the watchdog."""
    monkeypatch.setenv("JARVIS_AUDIO_SILENCE_TIMEOUT_S", "0")
    from resilience import audio_silence_watchdog as mod
    importlib.reload(mod)
    task = mod.start_audio_silence_watchdog_task()
    assert task is None
