"""Tests for tools/computer_loop.py — the iterate-until-done driver.

All Anthropic calls are scripted via `_anthropic_call`. Backend ops
are mocked at the module level so no real xdotool fires. AT-SPI is
mocked to return [].
"""
import asyncio
from dataclasses import dataclass
from typing import Any

import pytest


@dataclass
class FakeUsage:
    input_tokens: int = 1000
    output_tokens: int = 50
    cache_read_input_tokens: int = 0


@dataclass
class FakeToolUse:
    name: str
    input: dict
    id: str = "toolu_xyz"
    type: str = "tool_use"


@dataclass
class FakeResponse:
    content: list
    usage: FakeUsage
    stop_reason: str = "tool_use"
    model: str = "claude-sonnet-4-6"


@pytest.fixture
def loop_env(monkeypatch, tmp_path):
    """Mock all I/O boundaries: anthropic_call, take_screenshot,
    enumerate_widgets, backend input ops, and the audit-row writer.
    Tests append scripted responses to `script`; loop pops from it
    in order."""
    from tools import computer_loop, computer_backend

    script: list[Any] = []
    calls: list[dict] = []

    async def fake_anthropic_call(**kw):
        calls.append(kw)
        return script[len(calls) - 1]

    async def fake_screenshot():
        # Return a tiny valid PNG (1x1)
        return (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
            b"\x00\x00\x00\rIDAT\x78\x9cc\xf8\xcf\xc0\x00\x00\x00"
            b"\x03\x00\x01\x9d\xfa$\x05\x00\x00\x00\x00IEND\xaeB`\x82"
        )

    def fake_scale(png):
        return png, 1.0, 1.0

    async def noop_click(*a, **kw): pass
    async def noop_type(*a, **kw): pass
    async def noop_key(*a, **kw): pass

    audit_rows: list[dict] = []
    def fake_log_action(**row):
        audit_rows.append(row)

    monkeypatch.setattr(computer_loop, "_anthropic_call", fake_anthropic_call)
    monkeypatch.setattr(computer_loop, "_take_screenshot", fake_screenshot)
    monkeypatch.setattr(computer_loop, "_scale_for_model", fake_scale)
    monkeypatch.setattr(computer_loop, "_enumerate_widgets", lambda: [])
    monkeypatch.setattr(computer_loop, "_backend_click", noop_click)
    monkeypatch.setattr(computer_loop, "_backend_type", noop_type)
    monkeypatch.setattr(computer_loop, "_backend_key", noop_key)
    monkeypatch.setattr(computer_loop, "_log_action", fake_log_action)

    return script, calls, audit_rows


@pytest.mark.asyncio
async def test_loop_happy_path_completes_after_two_steps(loop_env):
    """One screenshot → one click → task_done."""
    from tools.computer_loop import run

    script, calls, audit = loop_env
    script.append(FakeResponse(
        content=[FakeToolUse("computer", {"action": "left_click", "coordinate": [50, 50]})],
        usage=FakeUsage(),
    ))
    script.append(FakeResponse(
        content=[FakeToolUse("computer", {"action": "task_done", "summary": "Done."})],
        usage=FakeUsage(),
    ))

    cancel = asyncio.Event()
    result = await run(
        task="click something",
        anthropic_client=None,
        safety_confirm_cb=lambda phrase: asyncio.sleep(0, result=True),
        cancel_event=cancel,
    )

    assert result.ok is True
    assert result.reason == "completed"
    assert result.summary == "Done."
    assert result.steps == 2
    assert len(audit) >= 2


@pytest.mark.asyncio
async def test_loop_bails_on_budget_breach(loop_env):
    """After the first call, cost_usd exceeds budget → bail with
    reason='budget'."""
    from tools.computer_loop import run

    script, calls, audit = loop_env
    # 1M input tokens × $3/M = $3 cost — way over the $0.10 budget
    script.append(FakeResponse(
        content=[FakeToolUse("computer", {"action": "left_click", "coordinate": [50, 50]})],
        usage=FakeUsage(input_tokens=1_000_000, output_tokens=0),
    ))

    cancel = asyncio.Event()
    result = await run(
        task="x", anthropic_client=None,
        safety_confirm_cb=lambda p: asyncio.sleep(0, result=True),
        cancel_event=cancel,
        budget_usd=0.10,
    )
    assert result.reason == "budget"
    assert result.cost_usd > 0.10


@pytest.mark.asyncio
async def test_loop_bails_on_max_iters(loop_env):
    """If max_iters=2 and model never emits task_done, bail."""
    from tools.computer_loop import run

    script, calls, audit = loop_env
    for _ in range(5):
        script.append(FakeResponse(
            content=[FakeToolUse("computer", {"action": "left_click", "coordinate": [10, 10]})],
            usage=FakeUsage(),
        ))

    cancel = asyncio.Event()
    result = await run(
        task="x", anthropic_client=None,
        safety_confirm_cb=lambda p: asyncio.sleep(0, result=True),
        cancel_event=cancel,
        max_iters=2,
    )
    assert result.reason == "max_iters"
    assert result.steps == 2


@pytest.mark.asyncio
async def test_loop_bails_on_cancel_event(loop_env, monkeypatch):
    """cancel_event.set() between iterations bails with reason=interrupted."""
    from tools.computer_loop import run

    script, calls, audit = loop_env
    cancel = asyncio.Event()

    # First response triggers cancel; second response would task_done
    # but we shouldn't get there.
    script.append(FakeResponse(
        content=[FakeToolUse("computer", {"action": "left_click", "coordinate": [10, 10]})],
        usage=FakeUsage(),
    ))
    script.append(FakeResponse(
        content=[FakeToolUse("computer", {"action": "task_done", "summary": "should not reach"})],
        usage=FakeUsage(),
    ))

    # Monkeypatch _execute_action to set the cancel event after the
    # first action runs.
    from tools import computer_loop
    orig = computer_loop._execute_action
    async def cancel_after(*a, **kw):
        cancel.set()
        return await orig(*a, **kw)
    monkeypatch.setattr(computer_loop, "_execute_action", cancel_after)

    result = await run(
        task="x", anthropic_client=None,
        safety_confirm_cb=lambda p: asyncio.sleep(0, result=True),
        cancel_event=cancel,
    )
    assert result.reason == "interrupted"


@pytest.mark.asyncio
async def test_loop_bails_on_wall_timeout(loop_env, monkeypatch):
    """wall_timeout_s triggers before the second API call."""
    from tools import computer_loop
    from tools.computer_loop import run

    script, calls, audit = loop_env
    # Provide one response for the first iteration
    script.append(FakeResponse(
        content=[FakeToolUse("computer", {"action": "left_click", "coordinate": [10, 10]})],
        usage=FakeUsage(),
    ))

    cancel = asyncio.Event()
    # Monkeypatch time.monotonic to return elapsed time > wall_timeout_s
    # The check happens at the start of iteration 2, before any API call
    import time as _t
    real_monotonic = _t.monotonic
    call_count = [0]
    base = real_monotonic()

    def fake_monotonic():
        call_count[0] += 1
        # On the first call (in run() initialization), return base time
        # On all subsequent calls, return base + 999 (way past wall_timeout_s=1.0)
        if call_count[0] == 1:
            return base
        return base + 999.0

    monkeypatch.setattr(computer_loop, "time", _t)
    monkeypatch.setattr(_t, "monotonic", fake_monotonic)

    result = await run(
        task="x", anthropic_client=None,
        safety_confirm_cb=lambda p: asyncio.sleep(0, result=True),
        cancel_event=cancel,
        wall_timeout_s=1.0,
    )
    monkeypatch.setattr(_t, "monotonic", real_monotonic)
    assert result.reason == "bailed"
    assert "timeout" in result.summary.lower()


@pytest.mark.asyncio
async def test_loop_escalates_sonnet_to_opus_after_no_progress(loop_env):
    """3 identical actions with no screenshot change → switch model
    from Sonnet to Opus on the 4th call."""
    from tools.computer_loop import run

    script, calls, audit = loop_env
    # 3 identical clicks (will trigger no-progress detection)
    for _ in range(3):
        script.append(FakeResponse(
            content=[FakeToolUse("computer", {"action": "left_click", "coordinate": [100, 100]})],
            usage=FakeUsage(),
        ))
    # 4th call should be on Opus; it returns task_done
    script.append(FakeResponse(
        content=[FakeToolUse("computer", {"action": "task_done", "summary": "done"})],
        usage=FakeUsage(),
        model="claude-opus-4-7",
    ))

    cancel = asyncio.Event()
    result = await run(
        task="x", anthropic_client=None,
        safety_confirm_cb=lambda p: asyncio.sleep(0, result=True),
        cancel_event=cancel,
        no_progress_escalation_after=3,
    )

    assert result.reason == "completed"
    # Calls 1-3 on sonnet, call 4 on opus
    assert calls[0]["model"] == "claude-sonnet-4-6"
    assert calls[1]["model"] == "claude-sonnet-4-6"
    assert calls[2]["model"] == "claude-sonnet-4-6"
    assert calls[3]["model"] == "claude-opus-4-7"


@pytest.mark.asyncio
async def test_loop_blocked_after_opus_also_stuck(loop_env):
    """3 identical actions on Sonnet, then 3 more on Opus → bail with
    reason='blocked'."""
    from tools.computer_loop import run

    script, calls, audit = loop_env
    for _ in range(6):
        script.append(FakeResponse(
            content=[FakeToolUse("computer", {"action": "left_click", "coordinate": [100, 100]})],
            usage=FakeUsage(),
        ))

    cancel = asyncio.Event()
    result = await run(
        task="x", anthropic_client=None,
        safety_confirm_cb=lambda p: asyncio.sleep(0, result=True),
        cancel_event=cancel,
        no_progress_escalation_after=3,
    )

    assert result.reason == "blocked"
