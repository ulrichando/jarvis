"""Tests for `tools/monitor.py` — background-command watcher tools.

Voice-adapted port of claude-code's Monitor tool (poll-based for
voice's user-initiated cadence rather than push-based). Uses real
subprocesses kept fast — `sleep 0.05`, `echo`, short for-loops.

All tests run inside one `asyncio` event loop via
`@pytest.mark.asyncio` because the reader task spawned during
`monitor_start` MUST share a loop with subsequent status/stop calls.
Earlier draft using `asyncio.run` per call lost the reader to a
destroyed loop and produced flaky timeouts.

The registry is worker-scoped in-memory state; each test resets it
via the `monitor_module` fixture.
"""
from __future__ import annotations

import asyncio
import re
import time

import pytest


@pytest.fixture
def monitor_module():
    """Fresh in-memory registry for each test."""
    from tools import monitor
    monitor.reset_for_test()
    yield monitor
    # Tidy up any monitors left running.
    for m in list(monitor._monitors.values()):
        try:
            if m.process.returncode is None:
                m.process.kill()
        except Exception:
            pass
    monitor.reset_for_test()


def _unwrap(tool):
    for attr in ("__livekit_agents_func", "_func", "fnc", "func", "callable"):
        f = getattr(tool, attr, None)
        if callable(f):
            return f
    if callable(tool):
        return tool
    raise RuntimeError(f"can't unwrap {tool!r}")


async def _wait_for_lines(monitor, want: int, timeout: float = 2.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if len(monitor.output) >= want:
            return
        await asyncio.sleep(0.02)
    raise TimeoutError(f"only saw {len(monitor.output)}/{want} lines after {timeout}s")


async def _wait_for_exit(monitor, timeout: float = 2.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if monitor.exit_code is not None:
            return
        await asyncio.sleep(0.02)
    raise TimeoutError(f"monitor {monitor.id} never set exit_code after {timeout}s")


# ── monitor_start ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_start_returns_id(monitor_module):
    out = await _unwrap(monitor_module.monitor_start)(
        command="echo hello", description="echo test"
    )
    assert "Monitor m1 started" in out
    assert "echo test" in out


@pytest.mark.asyncio
async def test_start_assigns_sequential_ids(monitor_module):
    for desc in ("A", "B", "C"):
        await _unwrap(monitor_module.monitor_start)(command="sleep 5", description=desc)
    out = await _unwrap(monitor_module.monitor_list)()
    assert "m1" in out and "m2" in out and "m3" in out


@pytest.mark.asyncio
async def test_start_empty_command_rejected(monitor_module):
    out = await _unwrap(monitor_module.monitor_start)(command="   ")
    assert "Empty command" in out


@pytest.mark.asyncio
async def test_start_uses_command_as_default_description(monitor_module):
    await _unwrap(monitor_module.monitor_start)(command="echo no-desc")
    out = await _unwrap(monitor_module.monitor_list)()
    assert "echo no-desc" in out


@pytest.mark.asyncio
async def test_start_cap_at_max_monitors(monitor_module):
    from tools.monitor import _MAX_MONITORS
    for i in range(_MAX_MONITORS):
        await _unwrap(monitor_module.monitor_start)(
            command="sleep 10", description=f"m{i}"
        )
    out = await _unwrap(monitor_module.monitor_start)(
        command="sleep 10", description="overflow"
    )
    assert "Too many monitors" in out


@pytest.mark.asyncio
async def test_start_supports_bashisms(monitor_module):
    """Run under /bin/bash -c, not /bin/sh — verify a bashism produces real output."""
    await _unwrap(monitor_module.monitor_start)(
        command="[[ 1 == 1 ]] && echo BASHISM_OK || echo BASHISM_FAIL"
    )
    monitor = list(monitor_module._monitors.values())[-1]
    await _wait_for_lines(monitor, 1, timeout=2.0)
    text = "\n".join(monitor.output)
    assert "BASHISM_OK" in text


# ── monitor_status ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_status_missing_id(monitor_module):
    out = await _unwrap(monitor_module.monitor_status)(monitor_id="m99")
    assert "not found" in out


@pytest.mark.asyncio
async def test_status_captures_output(monitor_module):
    await _unwrap(monitor_module.monitor_start)(
        command='for i in 1 2 3 4 5; do echo "line $i"; done',
        description="five lines",
    )
    monitor = list(monitor_module._monitors.values())[-1]
    await _wait_for_lines(monitor, 5)
    out = await _unwrap(monitor_module.monitor_status)(monitor_id="m1", lines=5)
    for n in ("line 1", "line 2", "line 5"):
        assert n in out


@pytest.mark.asyncio
async def test_status_lines_arg_caps_at_buffer_max(monitor_module):
    """Passing lines=99999 should clamp to _MAX_OUTPUT_LINES, not crash."""
    await _unwrap(monitor_module.monitor_start)(command="echo a")
    out = await _unwrap(monitor_module.monitor_status)(monitor_id="m1", lines=99999)
    assert "Monitor m1" in out


@pytest.mark.asyncio
async def test_status_no_output_yet(monitor_module):
    """A monitor that hasn't produced anything reads as `(no output yet)`."""
    await _unwrap(monitor_module.monitor_start)(command="sleep 5", description="quiet")
    out = await _unwrap(monitor_module.monitor_status)(monitor_id="m1")
    assert "(no output yet)" in out
    assert "running" in out


@pytest.mark.asyncio
async def test_status_shows_exit_state_after_completion(monitor_module):
    await _unwrap(monitor_module.monitor_start)(command="true")
    monitor = monitor_module._monitors["m1"]
    await _wait_for_exit(monitor)
    out = await _unwrap(monitor_module.monitor_status)(monitor_id="m1")
    assert "exited (code 0)" in out


@pytest.mark.asyncio
async def test_status_nonzero_exit_code_captured(monitor_module):
    await _unwrap(monitor_module.monitor_start)(command="exit 42")
    monitor = monitor_module._monitors["m1"]
    await _wait_for_exit(monitor)
    out = await _unwrap(monitor_module.monitor_status)(monitor_id="m1")
    assert "exited (code 42)" in out


# ── monitor_stop ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stop_kills_running_monitor(monitor_module):
    await _unwrap(monitor_module.monitor_start)(command="sleep 30")
    out = await _unwrap(monitor_module.monitor_stop)(monitor_id="m1")
    assert "stopped" in out
    proc = monitor_module._monitors["m1"].process
    assert proc.returncode is not None


@pytest.mark.asyncio
async def test_stop_missing_id(monitor_module):
    out = await _unwrap(monitor_module.monitor_stop)(monitor_id="m99")
    assert "not found" in out


@pytest.mark.asyncio
async def test_stop_already_exited(monitor_module):
    await _unwrap(monitor_module.monitor_start)(command="true")
    monitor = monitor_module._monitors["m1"]
    await _wait_for_exit(monitor)
    out = await _unwrap(monitor_module.monitor_stop)(monitor_id="m1")
    assert "already exited" in out


# ── monitor_list ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_empty(monitor_module):
    assert await _unwrap(monitor_module.monitor_list)() == "No monitors active."


@pytest.mark.asyncio
async def test_list_shows_state_per_monitor(monitor_module):
    await _unwrap(monitor_module.monitor_start)(command="sleep 30", description="long-runner")
    await _unwrap(monitor_module.monitor_start)(command="true", description="quick-exit")
    await _wait_for_exit(monitor_module._monitors["m2"])
    out = await _unwrap(monitor_module.monitor_list)()
    assert "m1" in out and "running" in out and "long-runner" in out
    assert "m2" in out and "exited" in out and "quick-exit" in out


# ── lifecycle ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_output_buffer_is_bounded(monitor_module):
    """500-line cap stops unbounded growth from a chatty long runner."""
    from tools.monitor import _MAX_OUTPUT_LINES
    await _unwrap(monitor_module.monitor_start)(
        command="for i in $(seq 1 600); do echo line $i; done"
    )
    monitor = list(monitor_module._monitors.values())[-1]
    await _wait_for_exit(monitor, timeout=4.0)
    assert len(monitor.output) == _MAX_OUTPUT_LINES, (
        f"buffer should cap at {_MAX_OUTPUT_LINES}, got {len(monitor.output)}"
    )
    # Newest line preserved
    assert "line 600" in monitor.output[-1]
    # Earliest retained line must NOT be "line 1" — eviction worked
    first = monitor.output[0]
    assert "line 1 " not in first and "line 1\n" not in first


@pytest.mark.asyncio
async def test_output_lines_have_timestamps(monitor_module):
    """Every captured line gets an HH:MM:SS prefix so the supervisor
    can voice 'at 14:32:01 the build went red'."""
    await _unwrap(monitor_module.monitor_start)(command="echo hello")
    monitor = list(monitor_module._monitors.values())[-1]
    await _wait_for_lines(monitor, 1)
    line = monitor.output[0]
    assert re.match(r"\d\d:\d\d:\d\d ", line), f"missing timestamp prefix: {line!r}"
