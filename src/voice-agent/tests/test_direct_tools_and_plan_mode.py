"""Tests for plan-mode tools ported from claude-code.

Covers:
  - plan_mode: enter/exit toggle, file persistence, write tools
               refuse during plan mode.

Note: bash/read/edit/write tool tests removed — those modules
(tools.bash, tools.file_read, tools.file_edit, tools.file_write)
were removed in the Hermes teardown; shell is now tools/terminal_tool.py
and file I/O is tools/file_tools.py.
"""
from __future__ import annotations

import asyncio

import pytest


# ── Helpers ──────────────────────────────────────────────────────────


def _call(tool, **kwargs) -> str:
    """Invoke a livekit @function_tool and return its result.

    LiveKit wraps the underlying coroutine as a FunctionTool object;
    `.fnc` gives the raw async callable. Works for our tools because
    they don't take a RunContext."""
    fn = tool._func  # FunctionTool stores the bare async callable here
    return asyncio.run(fn(**kwargs))


@pytest.fixture(autouse=True)
def _reset_plan_mode():
    """Ensure each test starts with plan mode off."""
    from tools import plan_mode

    plan_mode._set_plan_mode(False)
    yield
    plan_mode._set_plan_mode(False)


# ── plan mode ────────────────────────────────────────────────────────


def test_enter_plan_mode_sets_flag():
    from tools.plan_mode import enter_plan_mode, is_in_plan_mode

    assert not is_in_plan_mode()
    out = _call(enter_plan_mode)
    assert "Plan mode enabled" in out
    assert is_in_plan_mode()


def test_exit_plan_mode_clears_flag_and_persists():
    from tools.plan_mode import (
        enter_plan_mode,
        exit_plan_mode,
        is_in_plan_mode,
        read_plan,
        get_plan_file_path,
    )

    _call(enter_plan_mode)
    assert is_in_plan_mode()
    plan_text = "1. Read jarvis_agent.py\n2. Add a tool\n3. Test"
    out = _call(exit_plan_mode, plan=plan_text)
    assert "Plan recorded" in out
    assert not is_in_plan_mode()

    # Plan persisted to disk.
    p = get_plan_file_path()
    assert p.exists()
    assert plan_text in p.read_text()

    # read_plan returns it.
    via_tool = _call(read_plan)
    assert plan_text in via_tool


def test_exit_plan_mode_rejects_empty_plan():
    from tools.plan_mode import enter_plan_mode, exit_plan_mode, is_in_plan_mode

    _call(enter_plan_mode)
    out = _call(exit_plan_mode, plan="")
    assert "plan is required" in out
    # Still in plan mode (didn't accept empty).
    assert is_in_plan_mode()


