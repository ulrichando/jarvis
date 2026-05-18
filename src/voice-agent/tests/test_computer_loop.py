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
