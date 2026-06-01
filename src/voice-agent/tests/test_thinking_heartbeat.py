"""Tests for the thinking-indicator heartbeat task."""
from __future__ import annotations

import asyncio
import time
<<<<<<< HEAD
import types
=======
>>>>>>> origin/master
from pathlib import Path

import pytest


def _file_age_s(p: Path) -> float:
    return time.time() - p.stat().st_mtime


@pytest.mark.asyncio
async def test_heartbeat_touches_file_repeatedly(tmp_path, monkeypatch):
    """Heartbeat keeps the file fresh — after 0.6s with 0.2s sleep
    interval, file mtime should be less than the heartbeat interval old."""
    from jarvis_agent import _thinking_heartbeat
    fake_file = tmp_path / ".agent-thinking"
    monkeypatch.setattr("jarvis_agent._AGENT_THINKING_FILE", fake_file)

    task = asyncio.create_task(_thinking_heartbeat(interval_s=0.2))
    try:
        # Let the heartbeat run for 3 ticks.
        await asyncio.sleep(0.65)
        assert fake_file.exists(), "heartbeat should have created the file"
        # The mtime should be within 0.3s (one interval + slack).
        assert _file_age_s(fake_file) < 0.3
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_heartbeat_unlinks_file_on_cancel(tmp_path, monkeypatch):
    from jarvis_agent import _thinking_heartbeat
    fake_file = tmp_path / ".agent-thinking"
    monkeypatch.setattr("jarvis_agent._AGENT_THINKING_FILE", fake_file)

    task = asyncio.create_task(_thinking_heartbeat(interval_s=0.1))
    await asyncio.sleep(0.2)
    assert fake_file.exists()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    # Cancellation must remove the file so the desktop indicator goes green.
    assert not fake_file.exists()


@pytest.mark.asyncio
async def test_heartbeat_survives_repeated_unlinks(tmp_path, monkeypatch):
    """Simulate the desktop racing the agent: file gets unlinked
    externally; heartbeat must re-create it on the next tick so the
    indicator doesn't blink green."""
    from jarvis_agent import _thinking_heartbeat
    fake_file = tmp_path / ".agent-thinking"
    monkeypatch.setattr("jarvis_agent._AGENT_THINKING_FILE", fake_file)

    task = asyncio.create_task(_thinking_heartbeat(interval_s=0.1))
    try:
        await asyncio.sleep(0.15)
        assert fake_file.exists()
        fake_file.unlink()
        await asyncio.sleep(0.2)
        assert fake_file.exists(), "heartbeat should re-touch after external unlink"
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_heartbeat_exits_cleanly_on_cancel_during_sleep(tmp_path, monkeypatch):
    """Cancel during the sleep portion of the loop — heartbeat must
    still unlink and exit (no hang)."""
    from jarvis_agent import _thinking_heartbeat
    fake_file = tmp_path / ".agent-thinking"
    monkeypatch.setattr("jarvis_agent._AGENT_THINKING_FILE", fake_file)

    task = asyncio.create_task(_thinking_heartbeat(interval_s=10.0))  # long sleep
    await asyncio.sleep(0.1)  # let it touch once
    assert fake_file.exists()
    task.cancel()
    try:
        await asyncio.wait_for(task, timeout=1.0)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass
    assert task.cancelled() or task.done()
    assert not fake_file.exists()


class _FakeSessionHB:
    """Stand-in for AgentSession.run-time. Only holds the heartbeat
    task slot the helpers manage."""
    def __init__(self):
        self._jarvis_thinking_heartbeat = None


@pytest.mark.asyncio
async def test_start_helper_creates_task_and_stores_on_session(tmp_path, monkeypatch):
    from jarvis_agent import _start_thinking_heartbeat
    monkeypatch.setattr("jarvis_agent._AGENT_THINKING_FILE", tmp_path / ".agent-thinking")
    sess = _FakeSessionHB()
    _start_thinking_heartbeat(sess, interval_s=0.1)
    try:
        assert sess._jarvis_thinking_heartbeat is not None
        assert not sess._jarvis_thinking_heartbeat.done()
        await asyncio.sleep(0.15)
        assert (tmp_path / ".agent-thinking").exists()
    finally:
        sess._jarvis_thinking_heartbeat.cancel()
        try:
            await sess._jarvis_thinking_heartbeat
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_start_helper_cancels_prior_task_defensively(tmp_path, monkeypatch):
    """Back-to-back calls — only the newest task runs."""
    from jarvis_agent import _start_thinking_heartbeat
    monkeypatch.setattr("jarvis_agent._AGENT_THINKING_FILE", tmp_path / ".agent-thinking")
    sess = _FakeSessionHB()
    _start_thinking_heartbeat(sess, interval_s=0.1)
    first = sess._jarvis_thinking_heartbeat
    _start_thinking_heartbeat(sess, interval_s=0.1)
    second = sess._jarvis_thinking_heartbeat
    # The second call must have cancelled the first and replaced it.
    await asyncio.sleep(0.05)
    assert first is not second
    assert first.cancelled() or first.done()
    assert not second.done()
    # Cleanup.
    second.cancel()
    try:
        await second
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_cancel_helper_handles_missing_task(tmp_path, monkeypatch):
    """If no heartbeat is running, cancel is a no-op."""
    from jarvis_agent import _cancel_thinking_heartbeat
    monkeypatch.setattr("jarvis_agent._AGENT_THINKING_FILE", tmp_path / ".agent-thinking")
    sess = _FakeSessionHB()
    # Should not raise.
    _cancel_thinking_heartbeat(sess)
    assert sess._jarvis_thinking_heartbeat is None


@pytest.mark.asyncio
async def test_cancel_helper_unlinks_file(tmp_path, monkeypatch):
    from jarvis_agent import _start_thinking_heartbeat, _cancel_thinking_heartbeat
    fake_file = tmp_path / ".agent-thinking"
    monkeypatch.setattr("jarvis_agent._AGENT_THINKING_FILE", fake_file)
    sess = _FakeSessionHB()
    _start_thinking_heartbeat(sess, interval_s=0.1)
    await asyncio.sleep(0.15)
    assert fake_file.exists()
    _cancel_thinking_heartbeat(sess)
    # Give the cancellation a moment to drain.
    await asyncio.sleep(0.05)
    assert not fake_file.exists()
    assert sess._jarvis_thinking_heartbeat is None
<<<<<<< HEAD


# ── Idle backstop cancel (2026-05-30) ──────────────────────────────────
# A turn can end with no final assistant item (the framework skips the
# reply because the current speech can't be interrupted), so _on_item
# never cancels the heartbeat. The backstop cancels it once the agent
# stays idle/listening past the grace.

class _FakeSessionIdle:
    def __init__(self, agent_state="listening"):
        self._jarvis_thinking_heartbeat = None
        self._jarvis_thinking_idle_cancel_task = None
        self.agent_state = agent_state


async def _drain(task):
    if task is None:
        return
    if not task.done():
        task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_idle_backstop_cancels_heartbeat_when_state_stays_idle(tmp_path, monkeypatch):
    """Turn ended with no final reply: state sits in 'listening', so after
    the grace the backstop cancels the orphaned heartbeat and unlinks the
    file → tray goes green."""
    from jarvis_agent import _start_thinking_heartbeat, _schedule_idle_heartbeat_cancel
    fake_file = tmp_path / ".agent-thinking"
    monkeypatch.setattr("jarvis_agent._AGENT_THINKING_FILE", fake_file)
    monkeypatch.setenv("JARVIS_THINKING_IDLE_GRACE_S", "0.15")
    sess = _FakeSessionIdle(agent_state="listening")
    _start_thinking_heartbeat(sess, interval_s=0.05)
    hb = sess._jarvis_thinking_heartbeat
    await asyncio.sleep(0.08)
    assert fake_file.exists()
    _schedule_idle_heartbeat_cancel(sess)
    assert sess._jarvis_thinking_idle_cancel_task is not None
    # Past the grace, with state still idle → heartbeat cancelled.
    await asyncio.sleep(0.3)
    assert hb.cancelled() or hb.done()
    assert sess._jarvis_thinking_heartbeat is None  # _cancel_* nulls the slot
    assert not fake_file.exists()
    await _drain(sess._jarvis_thinking_idle_cancel_task)


@pytest.mark.asyncio
async def test_idle_backstop_skips_when_state_left_idle(tmp_path, monkeypatch):
    """If the agent went back to active work by the time the grace fires,
    the backstop must NOT cancel the heartbeat (turn is still live)."""
    from jarvis_agent import _start_thinking_heartbeat, _schedule_idle_heartbeat_cancel
    fake_file = tmp_path / ".agent-thinking"
    monkeypatch.setattr("jarvis_agent._AGENT_THINKING_FILE", fake_file)
    monkeypatch.setenv("JARVIS_THINKING_IDLE_GRACE_S", "0.15")
    sess = _FakeSessionIdle(agent_state="listening")
    _start_thinking_heartbeat(sess, interval_s=0.05)
    _schedule_idle_heartbeat_cancel(sess)
    # Turn resumed: state moves back to thinking before the grace elapses.
    sess.agent_state = "thinking"
    await asyncio.sleep(0.3)
    assert not sess._jarvis_thinking_heartbeat.done()
    assert fake_file.exists()
    await _drain(sess._jarvis_thinking_idle_cancel_task)
    await _drain(sess._jarvis_thinking_heartbeat)


@pytest.mark.asyncio
async def test_pending_idle_cancel_aborted_on_resume(tmp_path, monkeypatch):
    """A return to thinking/speaking aborts the pending backstop task, so
    the heartbeat keeps running for the rest of the turn."""
    from jarvis_agent import (
        _start_thinking_heartbeat,
        _schedule_idle_heartbeat_cancel,
        _cancel_pending_idle_heartbeat_cancel,
    )
    fake_file = tmp_path / ".agent-thinking"
    monkeypatch.setattr("jarvis_agent._AGENT_THINKING_FILE", fake_file)
    monkeypatch.setenv("JARVIS_THINKING_IDLE_GRACE_S", "0.15")
    sess = _FakeSessionIdle(agent_state="listening")
    _start_thinking_heartbeat(sess, interval_s=0.05)
    _schedule_idle_heartbeat_cancel(sess)
    pending = sess._jarvis_thinking_idle_cancel_task
    assert pending is not None
    _cancel_pending_idle_heartbeat_cancel(sess)
    assert sess._jarvis_thinking_idle_cancel_task is None
    await asyncio.sleep(0.3)
    # Heartbeat survives; file still fresh.
    assert not sess._jarvis_thinking_heartbeat.done()
    assert fake_file.exists()
    assert pending.cancelled() or pending.done()
    await _drain(sess._jarvis_thinking_heartbeat)


@pytest.mark.asyncio
async def test_schedule_idle_cancel_noop_without_heartbeat(tmp_path, monkeypatch):
    """No heartbeat running → scheduling the backstop is a no-op."""
    from jarvis_agent import _schedule_idle_heartbeat_cancel
    monkeypatch.setattr("jarvis_agent._AGENT_THINKING_FILE", tmp_path / ".agent-thinking")
    sess = _FakeSessionIdle(agent_state="listening")
    _schedule_idle_heartbeat_cancel(sess)
    assert sess._jarvis_thinking_idle_cancel_task is None


def test_thinking_idle_grace_s_parsing(monkeypatch):
    from jarvis_agent import _thinking_idle_grace_s
    monkeypatch.delenv("JARVIS_THINKING_IDLE_GRACE_S", raising=False)
    assert _thinking_idle_grace_s() == 5.0
    monkeypatch.setenv("JARVIS_THINKING_IDLE_GRACE_S", "8.5")
    assert _thinking_idle_grace_s() == 8.5
    for bad in ("abc", "0", "-3", ""):
        monkeypatch.setenv("JARVIS_THINKING_IDLE_GRACE_S", bad)
        assert _thinking_idle_grace_s() == 5.0


# ── Heartbeat orphan watchdog (2026-05-30) ─────────────────────────────
# agent_state-INDEPENDENT backstop: a turn can wedge agent_state non-idle
# (a non-interruptible TTS whose playout never completes), so the idle
# backstop never fires. The heartbeat self-cancels when no genuine turn
# progress (_bump_turn_activity) lands for _thinking_max_idle_s() AND no
# tool is running.

def test_thinking_max_idle_s_parsing(monkeypatch):
    from jarvis_agent import _thinking_max_idle_s
    monkeypatch.delenv("JARVIS_THINKING_MAX_IDLE_S", raising=False)
    assert _thinking_max_idle_s() == 120.0
    monkeypatch.setenv("JARVIS_THINKING_MAX_IDLE_S", "30")
    assert _thinking_max_idle_s() == 30.0
    for bad in ("abc", "0", "-5", ""):
        monkeypatch.setenv("JARVIS_THINKING_MAX_IDLE_S", bad)
        assert _thinking_max_idle_s() == 120.0


def test_bump_turn_activity_sets_timestamp():
    from jarvis_agent import _bump_turn_activity
    sess = types.SimpleNamespace()
    before = time.monotonic()
    _bump_turn_activity(sess)
    assert sess._jarvis_last_turn_activity >= before


@pytest.mark.asyncio
async def test_watchdog_self_cancels_when_progress_stale(tmp_path, monkeypatch):
    """No turn progress for > max_idle AND no tool running → heartbeat
    self-cancels and unlinks the flag, even though agent_state is never
    consulted."""
    from jarvis_agent import _thinking_heartbeat
    fake_file = tmp_path / ".agent-thinking"
    tool_file = tmp_path / ".tool-running"           # absent
    monkeypatch.setattr("jarvis_agent._AGENT_THINKING_FILE", fake_file)
    monkeypatch.setattr("jarvis_agent._TOOL_BUSY_FILE", tool_file)
    monkeypatch.setenv("JARVIS_THINKING_MAX_IDLE_S", "0.2")
    sess = types.SimpleNamespace(_jarvis_last_turn_activity=time.monotonic() - 1.0)
    task = asyncio.create_task(_thinking_heartbeat(interval_s=0.05, session=sess))
    await asyncio.sleep(0.3)
    assert task.done()              # watchdog stopped it
    assert not fake_file.exists()   # and unlinked the flag


@pytest.mark.asyncio
async def test_watchdog_held_off_by_running_tool(tmp_path, monkeypatch):
    """Stale progress BUT a tool is running → keep the indicator amber
    (don't clear during a long legit tool)."""
    from jarvis_agent import _thinking_heartbeat
    fake_file = tmp_path / ".agent-thinking"
    tool_file = tmp_path / ".tool-running"
    tool_file.write_text("run_jarvis_cli\n123\n")     # tool busy
    monkeypatch.setattr("jarvis_agent._AGENT_THINKING_FILE", fake_file)
    monkeypatch.setattr("jarvis_agent._TOOL_BUSY_FILE", tool_file)
    monkeypatch.setenv("JARVIS_THINKING_MAX_IDLE_S", "0.2")
    sess = types.SimpleNamespace(_jarvis_last_turn_activity=time.monotonic() - 1.0)
    task = asyncio.create_task(_thinking_heartbeat(interval_s=0.05, session=sess))
    await asyncio.sleep(0.3)
    assert not task.done()          # held off
    assert fake_file.exists()       # still amber
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_watchdog_survives_while_progress_is_fresh(tmp_path, monkeypatch):
    """Ongoing progress keeps the heartbeat alive past max_idle."""
    from jarvis_agent import _thinking_heartbeat
    fake_file = tmp_path / ".agent-thinking"
    tool_file = tmp_path / ".tool-running"
    monkeypatch.setattr("jarvis_agent._AGENT_THINKING_FILE", fake_file)
    monkeypatch.setattr("jarvis_agent._TOOL_BUSY_FILE", tool_file)
    monkeypatch.setenv("JARVIS_THINKING_MAX_IDLE_S", "0.2")
    sess = types.SimpleNamespace(_jarvis_last_turn_activity=time.monotonic())
    task = asyncio.create_task(_thinking_heartbeat(interval_s=0.05, session=sess))
    for _ in range(6):              # bump faster than max_idle for ~0.3s
        await asyncio.sleep(0.05)
        sess._jarvis_last_turn_activity = time.monotonic()
    assert not task.done()          # survived past max_idle thanks to bumps
    assert fake_file.exists()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_watchdog_inert_without_session(tmp_path, monkeypatch):
    """session=None preserves the original always-on behavior (no watchdog)."""
    from jarvis_agent import _thinking_heartbeat
    fake_file = tmp_path / ".agent-thinking"
    monkeypatch.setattr("jarvis_agent._AGENT_THINKING_FILE", fake_file)
    monkeypatch.setenv("JARVIS_THINKING_MAX_IDLE_S", "0.1")
    task = asyncio.create_task(_thinking_heartbeat(interval_s=0.05))  # no session
    await asyncio.sleep(0.3)        # well past max_idle
    assert not task.done()          # no session → no watchdog → still running
    assert fake_file.exists()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
=======
>>>>>>> origin/master
