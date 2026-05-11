"""Tests for the Gemini-primary / Kimi-fallback vision dispatcher.

Gemini was the cloud backend pre-2026-05-11, swapped to Kimi when the
GCP project's Generative Language API returned 403s, then restored
2026-05-11 evening after the user reported Kimi was too slow
(~11s vs Gemini's ~3-4s for the same one-shot screenshot describe).

The restoration keeps Kimi as an automatic fallback so the original
API-disabled foot-gun can't silence vision again. These tests pin
both halves of that contract: Gemini runs by default, but specific
failure markers (API-disabled / quota / 5xx) trigger the Kimi path
silently.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools import _vision_backend as vb


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ── resolved_vision_backend ──────────────────────────────────────────


class TestResolvedBackend:
    """resolved_vision_backend() must honor explicit env values and
    pick gemini (not kimi) on auto when Ollama isn't local."""

    def test_explicit_gemini_returns_gemini(self):
        with patch.object(vb, "VISION_BACKEND", "gemini"):
            assert vb.resolved_vision_backend() == "gemini"

    def test_explicit_kimi_returns_kimi(self):
        with patch.object(vb, "VISION_BACKEND", "kimi"):
            assert vb.resolved_vision_backend() == "kimi"

    def test_explicit_ollama_returns_ollama(self):
        with patch.object(vb, "VISION_BACKEND", "ollama"):
            assert vb.resolved_vision_backend() == "ollama"

    def test_auto_with_ollama_reachable_picks_ollama(self):
        with patch.object(vb, "VISION_BACKEND", "auto"), \
             patch.object(vb, "ollama_reachable", return_value=True):
            assert vb.resolved_vision_backend() == "ollama"

    def test_auto_without_ollama_picks_gemini_not_kimi(self):
        """The whole point of the 2026-05-11 evening swap-back."""
        with patch.object(vb, "VISION_BACKEND", "auto"), \
             patch.object(vb, "ollama_reachable", return_value=False):
            assert vb.resolved_vision_backend() == "gemini"


# ── Gemini fallback-marker classification ────────────────────────────


class TestGeminiFallbackMarkers:
    """The markers cover known Gemini failure modes we want to silently
    route to Kimi instead of letting bubble up."""

    @pytest.mark.parametrize("msg", [
        "API is disabled",
        "Generative Language API has not been used in project 12345",
        "Permission denied: missing IAM",
        "Quota exceeded for quota metric ...",
        "Rate limit exceeded",
        "Billing not enabled",
        "1011 INTERNAL",
        "503 Service Unavailable",
        "504 Deadline exceeded",
        # Case-insensitive
        "QUOTA EXCEEDED",
        "BILLING required",
        # The exact 2026-05-11 evening message from AI Studio when
        # the user's Gemini prepay credits are gone.
        "429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': "
        "'Your prepayment credits are depleted. Please go to AI Studio'}}",
    ])
    def test_marker_match(self, msg):
        assert vb._is_gemini_fallback_error(Exception(msg)) is True

    @pytest.mark.parametrize("msg", [
        "Bad request: prompt is empty",
        "Image is too large",
        "Invalid mime_type",
        "Connection refused",  # genuine network issue, surface up
        "ValueError: image_bytes must be bytes",
    ])
    def test_marker_no_match(self, msg):
        """Real bugs / programming errors must NOT silently fall through —
        they should surface so they get fixed."""
        assert vb._is_gemini_fallback_error(Exception(msg)) is False


# ── vision_describe dispatch ─────────────────────────────────────────


class TestVisionDescribeDispatch:
    """End-to-end behavior of the dispatcher under each backend +
    failure mode."""

    def _gemini_async(self, retval="from gemini"):
        return AsyncMock(return_value=retval)

    def _kimi_async(self, retval="from kimi"):
        return AsyncMock(return_value=retval)

    def _ollama_async(self, retval="from ollama"):
        return AsyncMock(return_value=retval)

    def test_explicit_gemini_calls_gemini_only(self):
        with patch.object(vb, "VISION_BACKEND", "gemini"), \
             patch.object(vb, "gemini_describe_raw", self._gemini_async()) as g, \
             patch.object(vb, "kimi_describe_raw", self._kimi_async()) as k:
            out = run(vb.vision_describe(b"\x89PNG"))
        assert out == "from gemini"
        g.assert_awaited_once()
        k.assert_not_awaited()

    def test_explicit_kimi_calls_kimi_only(self):
        with patch.object(vb, "VISION_BACKEND", "kimi"), \
             patch.object(vb, "gemini_describe_raw", self._gemini_async()) as g, \
             patch.object(vb, "kimi_describe_raw", self._kimi_async()) as k:
            out = run(vb.vision_describe(b"\x89PNG"))
        assert out == "from kimi"
        g.assert_not_awaited()
        k.assert_awaited_once()

    def test_auto_falls_back_from_ollama_to_gemini(self):
        bad_ollama = AsyncMock(side_effect=ConnectionError("ollama oom"))
        with patch.object(vb, "VISION_BACKEND", "auto"), \
             patch.object(vb, "ollama_reachable", return_value=True), \
             patch.object(vb, "ollama_describe", bad_ollama), \
             patch.object(vb, "gemini_describe_raw", self._gemini_async()) as g, \
             patch.object(vb, "kimi_describe_raw", self._kimi_async()) as k:
            out = run(vb.vision_describe(b"\x89PNG"))
        assert out == "from gemini"
        bad_ollama.assert_awaited_once()
        g.assert_awaited_once()
        k.assert_not_awaited()

    def test_gemini_api_disabled_silently_falls_to_kimi(self):
        """The reason Kimi is retained as a fallback — the exact
        2026-05-11-morning failure must auto-route to Kimi instead
        of surfacing 403s to the user."""
        bad_gemini = AsyncMock(side_effect=Exception("API is disabled"))
        with patch.object(vb, "VISION_BACKEND", "auto"), \
             patch.object(vb, "ollama_reachable", return_value=False), \
             patch.object(vb, "gemini_describe_raw", bad_gemini) as g, \
             patch.object(vb, "kimi_describe_raw", self._kimi_async()) as k:
            out = run(vb.vision_describe(b"\x89PNG"))
        assert out == "from kimi"
        g.assert_awaited_once()
        k.assert_awaited_once()

    def test_gemini_quota_silently_falls_to_kimi(self):
        bad_gemini = AsyncMock(side_effect=Exception("Quota exceeded"))
        with patch.object(vb, "VISION_BACKEND", "gemini"), \
             patch.object(vb, "gemini_describe_raw", bad_gemini), \
             patch.object(vb, "kimi_describe_raw", self._kimi_async()) as k:
            out = run(vb.vision_describe(b"\x89PNG"))
        assert out == "from kimi"
        k.assert_awaited_once()

    def test_gemini_unknown_error_surfaces_not_falls_to_kimi(self):
        """Genuine bugs (TypeError, ValueError, network refused)
        must surface — silent fallback would hide them."""
        bad_gemini = AsyncMock(side_effect=ValueError("image_bytes must be bytes"))
        with patch.object(vb, "VISION_BACKEND", "gemini"), \
             patch.object(vb, "gemini_describe_raw", bad_gemini), \
             patch.object(vb, "kimi_describe_raw", self._kimi_async()) as k:
            with pytest.raises(ValueError, match="image_bytes"):
                run(vb.vision_describe(b"\x89PNG"))
        k.assert_not_awaited()

    def test_kimi_failure_bubbles_up(self):
        """Kimi is the last resort — its failures must surface, no
        further fallback layer below."""
        bad_kimi = AsyncMock(side_effect=ConnectionError("kimi offline"))
        with patch.object(vb, "VISION_BACKEND", "kimi"), \
             patch.object(vb, "kimi_describe_raw", bad_kimi):
            with pytest.raises(ConnectionError, match="kimi offline"):
                run(vb.vision_describe(b"\x89PNG"))


# ── Smoke: get_gemini_client ─────────────────────────────────────────


class TestGetGeminiClient:
    def test_raises_when_key_missing(self):
        from tools.computer_use import ComputerUseError
        with patch.dict("os.environ", {"GOOGLE_API_KEY": ""}, clear=False):
            with pytest.raises(ComputerUseError, match="GOOGLE_API_KEY"):
                vb.get_gemini_client()

    def test_returns_client_when_key_present(self):
        # We mock genai.Client to avoid making any real network setup.
        mock_client = MagicMock(name="GenaiClient")
        with patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key-stub"}, clear=False), \
             patch("google.genai.Client", return_value=mock_client) as Client:
            client = vb.get_gemini_client()
        Client.assert_called_once_with(api_key="test-key-stub")
        assert client is mock_client
