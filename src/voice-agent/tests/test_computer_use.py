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
