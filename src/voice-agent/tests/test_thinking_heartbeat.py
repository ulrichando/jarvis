"""Tests for the thinking-indicator heartbeat task."""
from __future__ import annotations

import asyncio
import time
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
