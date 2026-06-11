"""Tests for launch_app — the verified-launch wrapper.

The function is decorated with @function_tool, so the actual
coroutine is at `launch_app._func`. We test the three return paths
(OK / MISSING / CRASHED) by monkeypatching the system primitives
the wrapper depends on:

  - shutil.which(bin) → None means MISSING (pre-flight catch)
  - asyncio.create_subprocess_shell → spawns the setsid -f command
  - tools.runtime.is_process_running → cross-platform process probe
                                       (post-launch verifier, replaces
                                       the pre-Phase-3.1 pgrep shellout)
  - log_launch_attempt              → telemetry side effect we verify

We also confirm the path / args / outcome strings match what the
desktop subagent's prompt teaches the LLM to interpret.
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def _get_launch_app():
    """Pull the underlying coroutine out of the @function_tool wrapper."""
    from jarvis_agent import launch_app
    return launch_app._func


class _FakeProcShell:
    """Stand-in for the spawned `setsid -f <bin>` proc.

    setsid forks immediately, so the wait() returns 0 fast regardless
    of whether the inner binary exec'd successfully — that's the
    whole reason verified launches need a separate pgrep step.
    """
    def __init__(self):
        self.returncode = 0

    async def wait(self):
        return 0


class _FakeProcPgrep:
    """Legacy stand-in for the pre-Phase-3.1 `pgrep -f <bin>` proc.

    Kept around for any test that may still reference it; the
    post-launch verifier now uses tools.runtime.is_process_running
    (psutil-backed) rather than shelling out to pgrep, so the
    OK/CRASHED tests monkeypatch the helper directly.
    """
    def __init__(self, output: bytes):
        self._output = output

    async def communicate(self):
        return self._output, b""


def _run(coro):
    """Tiny helper so we don't need pytest-asyncio for these tests."""
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


# ── MISSING path ──────────────────────────────────────────────────────


def test_missing_binary_returns_missing(monkeypatch):
    """shutil.which returning None should short-circuit before any
    subprocess spawn. The fast-path is the whole reason this tool
    exists — Linux has no `notepad`, and bash `setsid -f notepad`
    silently exits 0."""
    import jarvis_agent
    import shutil
    monkeypatch.setattr(shutil, "which", lambda b: None)

    log_calls = []
    monkeypatch.setattr(
        jarvis_agent, "log_launch_attempt",
        lambda **kw: log_calls.append(kw),
    )

    result = _run(_get_launch_app()("notepad"))
    assert result.startswith("MISSING:")
    assert "notepad" in result
    assert log_calls == [{"binary": "notepad", "outcome": "MISSING"}]


def test_missing_binary_with_args_only_logs_binary_name(monkeypatch):
    """The `args` parameter shouldn't pollute the binary lookup or
    the telemetry row — only the bare binary name is logged."""
    import jarvis_agent, shutil
    monkeypatch.setattr(shutil, "which", lambda b: None)
    log_calls = []
    monkeypatch.setattr(jarvis_agent, "log_launch_attempt",
                        lambda **kw: log_calls.append(kw))

    result = _run(_get_launch_app()("paint", '--mode "fancy"'))
    assert result.startswith("MISSING:")
    assert log_calls[0]["binary"] == "paint"


def test_empty_binary_returns_missing(monkeypatch):
    """No binary supplied at all → MISSING with a 'no binary' message."""
    log_calls = []
    import jarvis_agent
    monkeypatch.setattr(jarvis_agent, "log_launch_attempt",
                        lambda **kw: log_calls.append(kw))
    result = _run(_get_launch_app()(""))
    # Empty binary doesn't go through shutil.which at all.
    assert result == "MISSING: no binary supplied"
    # Empty binary is filtered before the log call (we don't want a
    # row for "binary=''").
    assert log_calls == []


# ── OK path ───────────────────────────────────────────────────────────


def test_ok_path_when_verifier_finds_pid(monkeypatch):
    """Happy path: shutil.which returns a real path, setsid spawns,
    sleep elapses, is_process_running returns a pid → OK."""
    import jarvis_agent
    import shutil, asyncio as aio
    from tools import runtime as _runtime

    monkeypatch.setattr(shutil, "which", lambda b: f"/usr/bin/{b}")

    async def fake_subprocess_shell(*args, **kwargs):
        return _FakeProcShell()

    # launch_app spawns via create_subprocess_EXEC on Linux (`setsid -f <bin>`),
    # NOT _shell — so exec is the primitive that must be stubbed. Patching only
    # _shell left the real `setsid -f xeyes` running: the test stayed green
    # (the verifier below is mocked) while leaking a detached xeyes window every
    # single run (incl. the verify-before-done Stop hook). Patch both.
    monkeypatch.setattr(aio, "create_subprocess_shell", fake_subprocess_shell)
    monkeypatch.setattr(aio, "create_subprocess_exec", fake_subprocess_shell)
    # Verifier returns a non-empty PID list → "process is alive"
    monkeypatch.setattr(_runtime, "is_process_running", lambda pat: [123456])

    # No-op the sleep so the test runs fast.
    async def fake_sleep(_):
        pass
    monkeypatch.setattr(aio, "sleep", fake_sleep)

    log_calls = []
    monkeypatch.setattr(jarvis_agent, "log_launch_attempt",
                        lambda **kw: log_calls.append(kw))

    result = _run(_get_launch_app()("xeyes"))
    assert result.startswith("OK:")
    assert "xeyes" in result
    assert log_calls == [{"binary": "xeyes", "outcome": "OK"}]


# ── CRASHED path ──────────────────────────────────────────────────────


def test_crashed_when_verifier_finds_nothing(monkeypatch):
    """Binary exists on PATH (shutil.which) and setsid spawns OK,
    but is_process_running returns [] for the full 4s budget → process
    exec'd then crashed. Surface stderr from the captured log."""
    import jarvis_agent
    import shutil, asyncio as aio
    from tools import runtime as _runtime

    monkeypatch.setattr(shutil, "which", lambda b: f"/usr/bin/{b}")

    async def fake_subprocess_shell(*args, **kwargs):
        return _FakeProcShell()

    # Stub the EXEC primitive (Linux `setsid -f`), not just _shell — else the
    # real spawn runs. See the xeyes-leak note in the OK test above.
    monkeypatch.setattr(aio, "create_subprocess_shell", fake_subprocess_shell)
    monkeypatch.setattr(aio, "create_subprocess_exec", fake_subprocess_shell)
    # Empty PID list every poll → CRASHED
    monkeypatch.setattr(_runtime, "is_process_running", lambda pat: [])

    async def fake_sleep(_):
        pass
    monkeypatch.setattr(aio, "sleep", fake_sleep)

    log_calls = []
    monkeypatch.setattr(jarvis_agent, "log_launch_attempt",
                        lambda **kw: log_calls.append(kw))

    result = _run(_get_launch_app()("brokenapp"))
    assert result.startswith("CRASHED:")
    assert "brokenapp" in result
    assert log_calls == [{"binary": "brokenapp", "outcome": "CRASHED"}]


# ── Edge cases ────────────────────────────────────────────────────────


def test_binary_with_path_uses_only_basename(monkeypatch):
    """If the LLM passes 'foo --bar baz' as the binary (mistakenly
    inlining args), we strip down to 'foo' before the shutil.which
    lookup. Otherwise shutil.which sees a non-existent path."""
    import shutil, jarvis_agent
    seen_lookups = []
    def fake_which(b):
        seen_lookups.append(b)
        return None
    monkeypatch.setattr(shutil, "which", fake_which)
    monkeypatch.setattr(jarvis_agent, "log_launch_attempt", lambda **kw: None)

    _run(_get_launch_app()("google-chrome --new-window"))
    assert seen_lookups == ["google-chrome"]


def test_log_attempt_failure_does_not_break_return(monkeypatch):
    """telemetry write errors must not propagate up to the LLM."""
    import jarvis_agent, shutil
    monkeypatch.setattr(shutil, "which", lambda b: None)

    def boom(**kw):
        raise RuntimeError("DB locked")
    monkeypatch.setattr(jarvis_agent, "log_launch_attempt", boom)

    # Should still return MISSING, not raise.
    result = _run(_get_launch_app()("notepad"))
    assert result.startswith("MISSING:")
