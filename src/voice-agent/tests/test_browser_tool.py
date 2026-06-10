"""Tests for the ``browser_task`` tool (isolated browser_use subprocess bridge).

Proves:
  (a) The tool self-registers in the registry with the right metadata.
  (b) check_fn is INERT when the isolated venv python is missing OR no LLM key
      is set — so the suite never launches a browser and no-key CI stays safe.
  (c) check_fn arms only when BOTH the interpreter exists AND a key is present.
  (d) With check_fn satisfied and asyncio.create_subprocess_exec MOCKED, the
      happy path parses a fake {"ok": true, "result": ...} and returns it.
  (e) Timeout, spawn failure, empty/garbled stdout, and runner-error payloads
      all return clean human-readable strings (handler never raises).
  (f) The module imports without pulling in ``browser_use`` (absent in voice venv).

NO real browser launch. NO real subprocess to the isolated venv. All process
interaction is mocked.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_VA_ROOT = Path(__file__).resolve().parent.parent
if str(_VA_ROOT) not in sys.path:
    sys.path.insert(0, str(_VA_ROOT))

import tools.browser as browser  # noqa: E402
from tools.registry import registry  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(args: dict) -> str:
    """Drive the async handler to completion and return its string result."""
    return asyncio.run(browser._handle_browser_task(args))


def _fake_proc(stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0,
               communicate_exc: Exception | None = None):
    """Build a fake asyncio subprocess whose communicate() is awaitable."""
    proc = MagicMock()
    if communicate_exc is not None:
        proc.communicate = AsyncMock(side_effect=communicate_exc)
    else:
        proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.kill = MagicMock()
    proc.wait = AsyncMock(return_value=returncode)
    proc.returncode = returncode
    return proc


def _force_armed(monkeypatch):
    """Make both gating preconditions pass without touching the real FS/env.

    The handler checks ``_isolated_python().exists()`` and ``_RUNNER_PATH``.
    Point both at paths reported as existing.
    """
    fake_py = Path("/nonexistent/browser-use-venv/bin/python")
    monkeypatch.setattr(browser, "_isolated_python", lambda: fake_py)
    monkeypatch.setattr(Path, "exists", lambda self: True)


# ---------------------------------------------------------------------------
# (a) Registration
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_registered(self):
        entry = registry.get_entry("browser_task")
        assert entry is not None

    def test_metadata(self):
        entry = registry.get_entry("browser_task")
        assert entry is not None
        assert entry.is_async is True
        assert entry.toolset == "browser"
        assert entry.check_fn is browser._check_browser_available
        # All four LLM keys are documented as the required-env hints.
        for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
            assert key in entry.requires_env
        assert "browser" in entry.description.lower()

    def test_schema_shape(self):
        entry = registry.get_entry("browser_task")
        params = entry.schema["parameters"]
        assert params["properties"]["task"]["type"] == "string"
        assert params["properties"]["max_steps"]["type"] == "integer"
        assert params["required"] == ["task"]


# ---------------------------------------------------------------------------
# (b)+(c) check_fn gating — never launches a browser in CI / no-key envs
# ---------------------------------------------------------------------------


class TestCheckFn:
    def test_false_when_isolated_python_missing(self, monkeypatch):
        # A key is present, but the interpreter is reported missing.
        monkeypatch.setattr(browser, "_isolated_python",
                            lambda: Path("/nonexistent/python"))
        monkeypatch.setattr(browser, "_has_llm_key", lambda: True)
        assert browser._check_browser_available() is False

    def test_false_when_no_key(self, monkeypatch):
        # Interpreter exists, but no LLM key is set.
        monkeypatch.setattr(browser, "_isolated_python",
                            lambda: Path("/nonexistent/python"))
        monkeypatch.setattr(Path, "exists", lambda self: True)
        for key in browser._LLM_ENV_KEYS:
            monkeypatch.delenv(key, raising=False)
        assert browser._check_browser_available() is False

    def test_true_when_python_and_key_present(self, monkeypatch):
        monkeypatch.setattr(browser, "_isolated_python",
                            lambda: Path("/nonexistent/python"))
        monkeypatch.setattr(Path, "exists", lambda self: True)
        for key in browser._LLM_ENV_KEYS:
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        assert browser._check_browser_available() is True

    def test_has_llm_key_detects_each_provider(self, monkeypatch):
        for key in browser._LLM_ENV_KEYS:
            monkeypatch.delenv(key, raising=False)
        assert browser._has_llm_key() is False
        monkeypatch.setenv("GOOGLE_API_KEY", "g-test")
        assert browser._has_llm_key() is True


# ---------------------------------------------------------------------------
# (d) Happy path — subprocess MOCKED, parses fake JSON result
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_parses_ok_result(self, monkeypatch):
        _force_armed(monkeypatch)
        payload = json.dumps({"ok": True, "result": "Top story: Hello World", "steps": 4})
        fake = _fake_proc(stdout=(payload + "\n").encode())
        create = AsyncMock(return_value=fake)
        monkeypatch.setattr(asyncio, "create_subprocess_exec", create)

        out = _run({"task": "go to news.ycombinator.com and read the top story", "max_steps": 10})

        assert "Top story: Hello World" in out
        assert "4 browser steps" in out
        # The isolated interpreter + runner path were passed as argv (no shell).
        argv = create.await_args.args
        assert argv[0].endswith("python")
        assert argv[1].endswith("runner.py")

    def test_sends_task_json_on_stdin(self, monkeypatch):
        _force_armed(monkeypatch)
        fake = _fake_proc(stdout=b'{"ok": true, "result": "done", "steps": 1}\n')
        monkeypatch.setattr(asyncio, "create_subprocess_exec",
                            AsyncMock(return_value=fake))

        _run({"task": "  visit example.com  ", "max_steps": 7})

        sent = fake.communicate.await_args.kwargs["input"]
        req = json.loads(sent.decode())
        assert req["task"] == "visit example.com"  # trimmed
        assert req["max_steps"] == 7
        assert req["headless"] is True

    def test_result_with_extra_stdout_noise_uses_last_line(self, monkeypatch):
        _force_armed(monkeypatch)
        noisy = b"some stray dependency chatter\n{\"ok\": true, \"result\": \"clean\", \"steps\": 2}\n"
        fake = _fake_proc(stdout=noisy)
        monkeypatch.setattr(asyncio, "create_subprocess_exec",
                            AsyncMock(return_value=fake))

        out = _run({"task": "go to example.com and read the page"})
        assert "clean" in out


# ---------------------------------------------------------------------------
# (e) Failure paths — every one returns a clean string, never raises
# ---------------------------------------------------------------------------


class TestFailurePaths:
    def test_runner_error_payload(self, monkeypatch):
        _force_armed(monkeypatch)
        fake = _fake_proc(stdout=b'{"ok": false, "error": "no LLM API key set"}\n')
        monkeypatch.setattr(asyncio, "create_subprocess_exec",
                            AsyncMock(return_value=fake))

        out = _run({"task": "go to example.com and read the page"})
        assert "failed" in out.lower()
        assert "no LLM API key set" in out

    def test_timeout_returns_clean_error_and_kills(self, monkeypatch):
        _force_armed(monkeypatch)
        fake = _fake_proc(communicate_exc=asyncio.TimeoutError())
        monkeypatch.setattr(asyncio, "create_subprocess_exec",
                            AsyncMock(return_value=fake))

        out = _run({"task": "go to example.com and read the page"})
        assert "timed out" in out.lower()
        # Hung runner must be reaped.
        fake.kill.assert_called_once()

    def test_empty_stdout_returns_clean_error(self, monkeypatch):
        _force_armed(monkeypatch)
        fake = _fake_proc(stdout=b"", stderr=b"boom traceback", returncode=1)
        monkeypatch.setattr(asyncio, "create_subprocess_exec",
                            AsyncMock(return_value=fake))

        out = _run({"task": "go to example.com and read the page"})
        assert "no output" in out.lower()

    def test_garbled_stdout_returns_clean_error(self, monkeypatch):
        _force_armed(monkeypatch)
        fake = _fake_proc(stdout=b"not json at all\n")
        monkeypatch.setattr(asyncio, "create_subprocess_exec",
                            AsyncMock(return_value=fake))

        out = _run({"task": "go to example.com and read the page"})
        assert "unparseable" in out.lower()

    def test_spawn_failure_returns_clean_error(self, monkeypatch):
        _force_armed(monkeypatch)
        monkeypatch.setattr(asyncio, "create_subprocess_exec",
                            AsyncMock(side_effect=OSError("no exec")))

        out = _run({"task": "go to example.com and read the page"})
        assert "failed to start" in out.lower()

    def test_empty_task_rejected_before_spawn(self, monkeypatch):
        # Should never spawn for an empty task.
        spawn = AsyncMock(side_effect=AssertionError("must not spawn"))
        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
        out = _run({"task": "   "})
        assert "non-empty" in out.lower()
        spawn.assert_not_called()

    def test_missing_isolated_python_short_circuits(self, monkeypatch):
        # Interpreter genuinely missing -> clean error, no spawn.
        monkeypatch.setattr(browser, "_isolated_python",
                            lambda: Path("/nonexistent/python"))
        spawn = AsyncMock(side_effect=AssertionError("must not spawn"))
        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
        out = _run({"task": "go to example.com and read the page"})
        assert "unavailable" in out.lower()
        spawn.assert_not_called()


# ---------------------------------------------------------------------------
# Wall-clock scaling — 2026-06 upgrade (timeout follows the step budget;
# the old fixed 180s killed legitimate 35/50-step flows mid-run)
# ---------------------------------------------------------------------------


class TestTaskTimeoutScaling:
    def test_scales_with_step_budget(self, monkeypatch):
        monkeypatch.delenv("JARVIS_BROWSER_TASK_TIMEOUT_S", raising=False)
        assert browser._task_timeout_s(15) == 180.0      # floor — quick lookups unchanged
        assert browser._task_timeout_s(35) == 360.0      # 45 + 9*35
        assert browser._task_timeout_s(50) == 495.0      # 45 + 9*50
        assert browser._task_timeout_s(100) == 600.0     # cap

    def test_env_override_wins(self, monkeypatch):
        monkeypatch.setenv("JARVIS_BROWSER_TASK_TIMEOUT_S", "42")
        assert browser._task_timeout_s(50) == 42.0

    def test_garbage_env_ignored(self, monkeypatch):
        monkeypatch.setenv("JARVIS_BROWSER_TASK_TIMEOUT_S", "garbage")
        assert browser._task_timeout_s(50) == 495.0


# ---------------------------------------------------------------------------
# Runner optional-kwarg compat filter — 2026-06 upgrade (unknown Agent kwargs
# degrade with a stderr note instead of failing the whole task)
# ---------------------------------------------------------------------------


def _import_runner():
    """Import browser_use_bridge.runner with its stream-redirect side effects
    contained (the module re-points sys.stdout/stderr at import — fine as a
    subprocess script, hostile inside pytest)."""
    saved_out, saved_err = sys.stdout, sys.stderr
    try:
        import browser_use_bridge.runner as runner
    finally:
        sys.stdout, sys.stderr = saved_out, saved_err
    return runner


class TestRunnerKwargFilter:
    def test_drops_unsupported_optional_kwargs(self):
        runner = _import_runner()

        class _Agent:
            def __init__(self, task, llm, browser_profile=None, use_thinking=False):
                pass

        kw = {"task": "t", "llm": object(), "browser_profile": None,
              "use_thinking": False, "flash_mode": True, "fallback_llm": object()}
        out = runner._filter_supported_kwargs(_Agent, dict(kw))
        assert "flash_mode" not in out
        assert "fallback_llm" not in out
        assert out["task"] == "t" and "use_thinking" in out

    def test_keeps_everything_when_agent_takes_var_kwargs(self):
        runner = _import_runner()

        class _Agent:
            def __init__(self, task, **kwargs):
                pass

        kw = {"task": "t", "flash_mode": True}
        assert runner._filter_supported_kwargs(_Agent, dict(kw)) == kw

    def test_required_kwargs_always_survive(self):
        runner = _import_runner()

        class _Agent:
            def __init__(self, task, llm):
                pass

        out = runner._filter_supported_kwargs(_Agent, {"task": "t", "llm": 1})
        assert out == {"task": "t", "llm": 1}


# ---------------------------------------------------------------------------
# (f) Import hygiene — voice venv has no browser_use
# ---------------------------------------------------------------------------


class TestImportHygiene:
    def test_tool_module_does_not_import_browser_use(self):
        # tools.browser is already imported at module top; importing it must not
        # have dragged browser_use into sys.modules (it's absent in the voice venv).
        # (browser_use_bridge.runner is imported by the kwarg-filter tests above,
        # but its browser_use imports are all lazy/in-function, so this holds.)
        assert "browser_use" not in sys.modules

    def test_runner_path_points_outside_tools_dir(self):
        # The runner lives in browser_use_bridge/, a sibling of tools/, so the
        # tools/*.py discovery glob never imports it.
        assert browser._RUNNER_PATH.parent.name == "browser_use_bridge"
        assert browser._RUNNER_PATH.name == "runner.py"
        assert browser._RUNNER_PATH.parent.parent.name == "voice-agent"
