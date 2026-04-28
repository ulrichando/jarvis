"""Unit tests for jarvis_computer_use — screenshot, Gemini, xdotool, session."""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Allow importing the module directly without installing the project
sys.path.insert(0, str(Path(__file__).parent.parent))


def run(coro):
    """Run a coroutine on a fresh event loop (works in pytest)."""
    return asyncio.new_event_loop().run_until_complete(coro)


# ── Screenshot ────────────────────────────────────────────────────────


class TestTakeScreenshot:
    def test_calls_scrot_with_z_flag(self):
        import jarvis_computer_use as cu

        mock_open = MagicMock()
        mock_open.return_value.__enter__.return_value.read.return_value = b"\x89PNG"
        mock_open.return_value.__exit__.return_value = False

        with patch("jarvis_computer_use.subprocess.run") as mock_run, \
             patch("builtins.open", mock_open):
            cu._take_screenshot()

        argv = mock_run.call_args.args[0]
        assert argv[0] == "scrot"
        assert "-z" in argv

    def test_returns_bytes(self):
        import jarvis_computer_use as cu

        mock_open = MagicMock()
        mock_open.return_value.__enter__.return_value.read.return_value = b"\x89PNG"
        mock_open.return_value.__exit__.return_value = False

        with patch("jarvis_computer_use.subprocess.run"), \
             patch("builtins.open", mock_open):
            result = cu._take_screenshot()

        assert isinstance(result, bytes)
        assert result == b"\x89PNG"


# ── Gemini describe ───────────────────────────────────────────────────


class TestGeminiDescribe:
    def test_calls_generate_content_with_correct_model(self):
        import jarvis_computer_use as cu

        mock_response = MagicMock()
        mock_response.text = "Chrome browser is open"
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        with patch("jarvis_computer_use._get_gemini_client", return_value=mock_client):
            run(cu._gemini_describe(b"\x89PNG"))

        call_kwargs = mock_client.models.generate_content.call_args.kwargs
        assert call_kwargs["model"] == cu.GEMINI_MODEL

    def test_returns_text_from_response(self):
        import jarvis_computer_use as cu

        mock_response = MagicMock()
        mock_response.text = "Desktop: Kitty terminal in foreground"
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        with patch("jarvis_computer_use._get_gemini_client", return_value=mock_client):
            result = run(cu._gemini_describe(b"\x89PNG"))

        assert result == "Desktop: Kitty terminal in foreground"

    def test_falls_back_when_response_text_is_none(self):
        import jarvis_computer_use as cu

        mock_response = MagicMock()
        mock_response.text = None
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        with patch("jarvis_computer_use._get_gemini_client", return_value=mock_client):
            result = run(cu._gemini_describe(b"\x89PNG"))

        assert "no description" in result.lower()

    def test_raises_when_api_key_missing(self):
        import jarvis_computer_use as cu

        with patch.dict(os.environ, {"GOOGLE_API_KEY": ""}):
            with pytest.raises(cu.ComputerUseError, match="GOOGLE_API_KEY"):
                cu._get_gemini_client()


# ── xdotool ───────────────────────────────────────────────────────────


def _fake_subprocess_exec(stdout: bytes = b"", returncode: int = 0):
    """Build an asyncio.create_subprocess_exec replacement that returns the given output."""
    async def _factory(*args, **kwargs):
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(stdout, b""))
        proc.returncode = returncode
        return proc
    return _factory


class TestXdotoolWrapper:
    def test_runs_xdotool_and_returns_stripped_stdout(self):
        import jarvis_computer_use as cu

        with patch("asyncio.create_subprocess_exec",
                   side_effect=_fake_subprocess_exec(b"  12345  \n")):
            result = run(cu._xdotool("getactivewindow"))

        assert result == "12345"

    def test_passes_args_through_to_xdotool(self):
        import jarvis_computer_use as cu

        captured = {}
        async def factory(*args, **kwargs):
            captured["args"] = args
            proc = MagicMock()
            proc.communicate = AsyncMock(return_value=(b"", b""))
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=factory):
            run(cu._xdotool("type", "hello"))

        assert captured["args"][0] == "xdotool"
        assert captured["args"][1] == "type"
        assert captured["args"][2] == "hello"


# ── computer_use / computer_stop ──────────────────────────────────────


class TestComputerUseSession:
    def setup_method(self):
        import jarvis_computer_use as cu
        cu._active_session = None

    def teardown_method(self):
        import jarvis_computer_use as cu
        cu._active_session = None

    def test_computer_use_starts_session(self):
        import jarvis_computer_use as cu

        with patch.object(cu, "_screenshot_and_describe",
                          AsyncMock(return_value="Chrome open")):
            result = run(cu.computer_use(task="book a flight"))

        assert cu._active_session is not None
        assert cu._active_session.task == "book a flight"
        assert "Chrome open" in result

    def test_computer_use_rejects_second_session(self):
        import jarvis_computer_use as cu
        cu._active_session = cu._Session(task="existing task")

        with patch.object(cu, "_screenshot_and_describe",
                          AsyncMock(return_value="screen")):
            result = run(cu.computer_use(task="new task"))

        assert "already active" in result
        assert cu._active_session.task == "existing task"

    def test_computer_use_clears_session_on_screenshot_failure(self):
        import jarvis_computer_use as cu

        with patch.object(cu, "_screenshot_and_describe",
                          AsyncMock(side_effect=RuntimeError("scrot failed"))):
            result = run(cu.computer_use(task="oops"))

        assert cu._active_session is None
        assert "failed" in result.lower()

    def test_computer_stop_clears_session(self):
        import jarvis_computer_use as cu
        cu._active_session = cu._Session(task="open browser")

        result = run(cu.computer_stop())

        assert cu._active_session is None
        assert "open browser" in result

    def test_computer_stop_when_no_session(self):
        import jarvis_computer_use as cu

        result = run(cu.computer_stop())

        assert "no active" in result


# ── Action tools (click, type_text, scroll, drag, key_press, wait) ────


class TestActionTools:
    def setup_method(self):
        import jarvis_computer_use as cu
        cu._active_session = cu._Session(task="test")

    def teardown_method(self):
        import jarvis_computer_use as cu
        cu._active_session = None

    def test_click_requires_active_session(self):
        import jarvis_computer_use as cu
        cu._active_session = None

        result = run(cu.click(x=100, y=200))

        assert "no active" in result

    def test_click_calls_xdotool_mousemove_and_click(self):
        import jarvis_computer_use as cu

        captured = []
        async def factory(*args, **kwargs):
            captured.append(args)
            proc = MagicMock()
            proc.communicate = AsyncMock(return_value=(b"", b""))
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=factory), \
             patch.object(cu, "_screenshot_and_describe",
                          AsyncMock(return_value="clicked")), \
             patch("asyncio.sleep", AsyncMock()):
            result = run(cu.click(x=100, y=200, button="left", count=1))

        # First xdotool call should be mousemove, second should be click
        assert any("mousemove" in a for a in captured[0])
        assert "100" in captured[0]
        assert "200" in captured[0]
        assert any("click" in a for a in captured[1])
        assert "success=True" in result

    def test_type_text_calls_xdotool_type(self):
        import jarvis_computer_use as cu

        captured = []
        async def factory(*args, **kwargs):
            captured.append(args)
            proc = MagicMock()
            proc.communicate = AsyncMock(return_value=(b"", b""))
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=factory), \
             patch.object(cu, "_screenshot_and_describe",
                          AsyncMock(return_value="typed")), \
             patch("asyncio.sleep", AsyncMock()):
            result = run(cu.type_text(text="hello world", enter=False))

        assert "type" in captured[0]
        assert "hello world" in captured[0]
        assert "success=True" in result

    def test_type_text_with_enter_presses_return(self):
        import jarvis_computer_use as cu

        captured = []
        async def factory(*args, **kwargs):
            captured.append(args)
            proc = MagicMock()
            proc.communicate = AsyncMock(return_value=(b"", b""))
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=factory), \
             patch.object(cu, "_screenshot_and_describe",
                          AsyncMock(return_value="typed+entered")), \
             patch("asyncio.sleep", AsyncMock()):
            run(cu.type_text(text="search query", enter=True))

        # Last call should be xdotool key Return
        assert any("key" in a for a in captured[-1])
        assert "Return" in captured[-1]

    def test_key_press_calls_xdotool_key(self):
        import jarvis_computer_use as cu

        captured = []
        async def factory(*args, **kwargs):
            captured.append(args)
            proc = MagicMock()
            proc.communicate = AsyncMock(return_value=(b"", b""))
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=factory), \
             patch.object(cu, "_screenshot_and_describe",
                          AsyncMock(return_value="opened tab")), \
             patch("asyncio.sleep", AsyncMock()):
            run(cu.key_press(keys="ctrl+t"))

        assert "key" in captured[0]
        assert "ctrl+t" in captured[0]

    def test_wait_sleeps_and_redescribes(self):
        import jarvis_computer_use as cu

        slept = []
        async def fake_sleep(s):
            slept.append(s)

        with patch("asyncio.sleep", side_effect=fake_sleep), \
             patch.object(cu, "_screenshot_and_describe",
                          AsyncMock(return_value="settled")):
            result = run(cu.wait(ms=750))

        assert 0.75 in slept
        assert "success=True" in result
        assert "settled" in result

    def test_screenshot_does_not_require_session(self):
        import jarvis_computer_use as cu
        cu._active_session = None

        with patch.object(cu, "_screenshot_and_describe",
                          AsyncMock(return_value="bare desktop")):
            result = run(cu.screenshot())

        assert result == "bare desktop"


# ── Safety guards ─────────────────────────────────────────────────────


class TestSafetyGuards:
    def setup_method(self):
        import jarvis_computer_use as cu
        cu._active_session = None

    def teardown_method(self):
        import jarvis_computer_use as cu
        cu._active_session = None

    def test_check_guards_raises_on_failure_limit(self):
        import jarvis_computer_use as cu
        cu._active_session = cu._Session(task="test")
        cu._active_session.consecutive_failures = cu._FAILURE_LIMIT

        with pytest.raises(cu.ComputerUseError, match="consecutive failures"):
            cu._check_guards()

    def test_check_guards_raises_on_stall(self):
        import jarvis_computer_use as cu
        cu._active_session = cu._Session(task="test")
        cu._active_session.last_change_at = time.monotonic() - cu._STALL_TIMEOUT_S - 1

        with pytest.raises(cu.ComputerUseError, match="no visible UI change"):
            cu._check_guards()

    def test_check_guards_silent_when_no_session(self):
        import jarvis_computer_use as cu

        cu._check_guards()  # should not raise

    def test_record_success_resets_failures(self):
        import jarvis_computer_use as cu
        cu._active_session = cu._Session(task="test")
        cu._active_session.consecutive_failures = 2

        cu._record_success("new screen state")

        assert cu._active_session.consecutive_failures == 0

    def test_record_success_updates_change_time_on_new_desc(self):
        import jarvis_computer_use as cu
        cu._active_session = cu._Session(task="test")
        cu._active_session.last_description = "old state"
        cu._active_session.last_change_at = time.monotonic() - 5
        old_change_at = cu._active_session.last_change_at

        cu._record_success("new state different from old")

        assert cu._active_session.last_change_at > old_change_at

    def test_record_failure_increments_counter(self):
        import jarvis_computer_use as cu
        cu._active_session = cu._Session(task="test")

        cu._record_failure()
        cu._record_failure()

        assert cu._active_session.consecutive_failures == 2

    def test_click_returns_failure_when_guard_trips(self):
        import jarvis_computer_use as cu
        cu._active_session = cu._Session(task="test")
        cu._active_session.consecutive_failures = cu._FAILURE_LIMIT

        result = run(cu.click(x=10, y=10))

        assert "success=False" in result
        assert "consecutive failures" in result
