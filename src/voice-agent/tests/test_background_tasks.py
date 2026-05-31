"""Tests for fire-and-forget background tasks: the in-process queue
(pipeline.background_tasks) and dispatch_agent's background=True path.

No real bin/jarvis run — the subprocess is mocked, like test_dispatch_agent.py.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("GROQ_API_KEY", "test-key-for-init")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")


def _make_fake_proc(stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0):
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.kill = MagicMock()
    proc.wait = AsyncMock(return_value=returncode)
    return proc


# ── pipeline.background_tasks unit tests ─────────────────────────────

def test_register_complete_drain_roundtrip():
    from pipeline import background_tasks as bg
    bg.reset()
    bg.register("t1", "research quantum")
    assert bg.active_count() == 1
    assert bg.active()[0]["description"] == "research quantum"

    # nothing to drain until completion
    assert bg.drain_announcements() == []

    bg.complete("t1", "Your research on quantum is done.")
    assert bg.active_count() == 0  # no longer running
    drained = bg.drain_announcements()
    assert drained == ["Your research on quantum is done."]
    # queue is emptied after a drain
    assert bg.drain_announcements() == []


def test_complete_with_none_announcement_is_silent():
    from pipeline import background_tasks as bg
    bg.reset()
    bg.register("t1", "x")
    bg.complete("t1", None, status="success")
    assert bg.drain_announcements() == []
    assert bg.active_count() == 0


def test_requeue_puts_announcement_back_at_front():
    from pipeline import background_tasks as bg
    bg.reset()
    bg._pending.extend(["second"])
    bg.requeue("first")
    assert bg.drain_announcements() == ["first", "second"]


def test_discard_removes_task_without_voicing():
    from pipeline import background_tasks as bg
    bg.reset()
    bg.register("t1", "x")
    bg.discard("t1")
    assert bg.active_count() == 0
    assert bg.drain_announcements() == []


# ── dispatch_agent background=True path ──────────────────────────────

@pytest.mark.asyncio
async def test_background_returns_immediately_without_awaiting_result(monkeypatch):
    """background=True must return an ack string instantly — NOT the subagent
    output — so the turn doesn't block. The real work runs in a spawned task."""
    from tools.dispatch_agent import handle_dispatch_agent
    from pipeline import background_tasks as bg
    bg.reset()

    # A subprocess that would take "forever" if awaited inline.
    fake_proc = MagicMock()
    fake_proc.returncode = 0
    started = asyncio.Event()

    async def slow_communicate():
        started.set()
        await asyncio.sleep(30)
        return b"late result\n", b""

    fake_proc.communicate = AsyncMock(side_effect=slow_communicate)
    fake_proc.kill = MagicMock()
    fake_proc.wait = AsyncMock(return_value=-9)
    monkeypatch.setattr("asyncio.create_subprocess_exec", AsyncMock(return_value=fake_proc))

    result = await asyncio.wait_for(
        handle_dispatch_agent({
            "subagent_type": "researcher", "task": "research X",
            "description": "research X", "background": True,
        }),
        timeout=2.0,  # would blow past this if it awaited the 30s subprocess
    )
    # Returned immediately with an ack, not the subagent stdout.
    assert "late result" not in result
    assert "background" in result.lower()
    # The task was actually registered + spawned.
    assert bg.active_count() == 1
    # Let the spawned runner reach the subprocess, then cancel + drain it
    # cleanly (no "task was destroyed but pending" warnings).
    await asyncio.wait_for(started.wait(), timeout=1.0)
    others = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    for t in others:
        t.cancel()
    await asyncio.gather(*others, return_exceptions=True)
    bg.reset()


@pytest.mark.asyncio
async def test_background_enqueues_announcement_on_success(monkeypatch):
    """When the background runner completes, it must enqueue a spoken
    announcement containing the result for the watcher to voice."""
    from tools.dispatch_agent import handle_dispatch_agent
    from pipeline import background_tasks as bg
    bg.reset()

    fake_proc = _make_fake_proc(stdout=b"Quantum supremacy reached in 2019.\n", returncode=0)
    monkeypatch.setattr("asyncio.create_subprocess_exec", AsyncMock(return_value=fake_proc))

    await handle_dispatch_agent({
        "subagent_type": "researcher", "task": "research quantum",
        "description": "quantum research", "background": True,
    })

    # Drain the spawned runner task(s) to completion.
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=3.0)

    anns = bg.drain_announcements()
    assert len(anns) == 1
    assert "Quantum supremacy reached in 2019." in anns[0]
    assert bg.active_count() == 0
    bg.reset()


@pytest.mark.asyncio
async def test_background_announces_failure_on_nonzero_exit(monkeypatch):
    from tools.dispatch_agent import handle_dispatch_agent
    from pipeline import background_tasks as bg
    bg.reset()

    fake_proc = _make_fake_proc(stdout=b"", stderr=b"boom\n", returncode=1)
    monkeypatch.setattr("asyncio.create_subprocess_exec", AsyncMock(return_value=fake_proc))

    await handle_dispatch_agent({
        "subagent_type": "researcher", "task": "x",
        "description": "doomed task", "background": True,
    })
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=3.0)

    anns = bg.drain_announcements()
    assert len(anns) == 1
    assert "doomed task" in anns[0]
    # A failure announcement should read as a failure, not a result.
    assert any(w in anns[0].lower() for w in ("fail", "couldn't", "could not", "error", "stopped"))
    bg.reset()


@pytest.mark.asyncio
async def test_background_respects_concurrency_cap(monkeypatch):
    """At the cap, a further background dispatch is refused (no new subprocess)."""
    from tools.dispatch_agent import handle_dispatch_agent
    from pipeline import background_tasks as bg
    bg.reset()
    monkeypatch.setenv("JARVIS_BG_TASK_MAX", "1")

    # Pre-fill the registry to the cap so the new dispatch is refused.
    bg.register("existing", "already running")

    spawn_mock = AsyncMock(return_value=_make_fake_proc(stdout=b"ok\n"))
    monkeypatch.setattr("asyncio.create_subprocess_exec", spawn_mock)

    result = await handle_dispatch_agent({
        "subagent_type": "researcher", "task": "x",
        "description": "overflow task", "background": True,
    })
    # Refused → no subprocess spawned, registry unchanged.
    spawn_mock.assert_not_called()
    assert bg.active_count() == 1
    assert "already" in result.lower() or "running" in result.lower()
    bg.reset()


@pytest.mark.asyncio
async def test_foreground_path_unchanged_by_background_flag_absence(monkeypatch):
    """Sanity: omitting background (or background=False) keeps the blocking,
    return-the-stdout behaviour intact."""
    from tools.dispatch_agent import handle_dispatch_agent
    from pipeline import background_tasks as bg
    bg.reset()
    fake_proc = _make_fake_proc(stdout=b"Found at x.py:1\n", returncode=0)
    monkeypatch.setattr("asyncio.create_subprocess_exec", AsyncMock(return_value=fake_proc))
    result = await handle_dispatch_agent({
        "subagent_type": "explore", "task": "find x", "description": "find x",
    })
    assert "Found at x.py:1" in result
    assert bg.active_count() == 0  # foreground never touches the bg registry
    bg.reset()
