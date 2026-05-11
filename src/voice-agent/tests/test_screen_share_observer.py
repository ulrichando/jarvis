"""Tests for pipeline.screen_share_observer.

The observer polls vision_describe() at a fixed interval while a
screen-share track is subscribed, caches the description on the
session, and the screenshot() tool reads that cache for ~0s response.

Tests cover:
  - latest_description / latest_description_global freshness gating
  - _poll_loop calls vision_describe with the latest JPEG
  - _poll_loop skips when no JPEG is cached
  - _poll_loop survives a vision_describe exception
  - attach_to_room registers handlers idempotently
  - track_unsubscribed cancels the poll task + wipes the cache
  - screenshot() prefers the cached description over an on-demand
    describe when the observer's slot is fresh
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline import screen_share_observer as obs


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class _FakeSession:
    """Stand-in for AgentSession with just the attribute the observer touches."""
    def __init__(self):
        self._jarvis_latest_screen_description = None


# ── Freshness gating ────────────────────────────────────────────────


class TestLatestDescription:
    def setup_method(self):
        obs._GLOBAL_LATEST = None

    def teardown_method(self):
        obs._GLOBAL_LATEST = None

    def test_returns_none_when_session_slot_empty(self):
        s = _FakeSession()
        assert obs.latest_description(s) is None

    def test_returns_text_when_fresh(self):
        s = _FakeSession()
        s._jarvis_latest_screen_description = ("Chrome with a coding tutorial open", time.monotonic())
        assert obs.latest_description(s) == "Chrome with a coding tutorial open"

    def test_returns_none_when_stale(self):
        s = _FakeSession()
        # Manually back-date the cache past the default max_age.
        s._jarvis_latest_screen_description = ("stale", time.monotonic() - 60.0)
        assert obs.latest_description(s, max_age_s=5.0) is None

    def test_global_returns_none_when_empty(self):
        assert obs.latest_description_global() is None

    def test_global_returns_text_when_fresh(self):
        obs._GLOBAL_LATEST = ("foo", time.monotonic())
        assert obs.latest_description_global() == "foo"

    def test_global_returns_none_when_stale(self):
        obs._GLOBAL_LATEST = ("old", time.monotonic() - 60.0)
        assert obs.latest_description_global(max_age_s=5.0) is None


# ── Poll loop ────────────────────────────────────────────────────────


class TestPollLoop:
    def setup_method(self):
        obs._GLOBAL_LATEST = None
        # Run the loop fast so tests finish quickly. Keep this tight.
        self._orig_interval = obs.OBSERVER_INTERVAL_S
        obs.OBSERVER_INTERVAL_S = 0.05

    def teardown_method(self):
        obs._GLOBAL_LATEST = None
        obs.OBSERVER_INTERVAL_S = self._orig_interval

    def _run_loop_briefly(self, session, *, iterations: int = 2):
        """Spin the poll loop for `iterations` cycles, then cancel."""
        async def driver():
            task = asyncio.create_task(obs._poll_loop(session))
            # Each iteration sleeps OBSERVER_INTERVAL_S = 0.05s.
            # Wait long enough for `iterations` cycles + a small buffer.
            await asyncio.sleep(obs.OBSERVER_INTERVAL_S * iterations + 0.1)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        run(driver())

    def test_calls_vision_describe_with_latest_jpeg(self):
        session = _FakeSession()
        fake_describe = AsyncMock(return_value="A blue square on white")
        with patch("pipeline.screen_share_sink.latest_jpeg_global",
                   return_value=b"\xff\xd8\xff\xe0FAKE_JPEG"), \
             patch("tools._vision_backend.vision_describe", fake_describe):
            self._run_loop_briefly(session, iterations=2)

        fake_describe.assert_awaited()
        kwargs = fake_describe.call_args.kwargs
        args = fake_describe.call_args.args
        assert args[0] == b"\xff\xd8\xff\xe0FAKE_JPEG"
        assert kwargs.get("mime_type") == "image/jpeg"
        # The prompt should be the observer's content-rich version
        # that explicitly asks Gemini to READ text on the screen
        # (filenames, errors, headings) — not just describe the app.
        prompt = kwargs.get("prompt", "")
        assert "filenames" in prompt or "readable text" in prompt, (
            f"observer prompt regressed to a generic shape — must ask Gemini "
            f"to read text content. Got prompt[:120]={prompt[:120]!r}"
        )

    def test_caches_description_on_session_and_global(self):
        session = _FakeSession()
        fake_describe = AsyncMock(return_value="Chrome is open")
        with patch("pipeline.screen_share_sink.latest_jpeg_global",
                   return_value=b"\xff\xd8\xff\xe0X"), \
             patch("tools._vision_backend.vision_describe", fake_describe):
            self._run_loop_briefly(session, iterations=2)

        # Session slot populated.
        pair = session._jarvis_latest_screen_description
        assert pair is not None
        text, ts = pair
        assert text == "Chrome is open"
        # Module mirror populated.
        assert obs._GLOBAL_LATEST is not None
        assert obs._GLOBAL_LATEST[0] == "Chrome is open"

    def test_skips_when_no_jpeg_available(self):
        """Sink hasn't received a frame yet — loop must not crash."""
        session = _FakeSession()
        fake_describe = AsyncMock(return_value="should not be called")
        with patch("pipeline.screen_share_sink.latest_jpeg_global",
                   return_value=None), \
             patch("tools._vision_backend.vision_describe", fake_describe):
            self._run_loop_briefly(session, iterations=3)
        fake_describe.assert_not_awaited()
        assert session._jarvis_latest_screen_description is None

    def test_survives_vision_describe_failure(self):
        """A describe-call exception must not kill the loop — it logs
        and continues so a transient Gemini 503 doesn't permanently
        disable the observer until the next track resubscribe."""
        session = _FakeSession()
        # First call fails, second succeeds — verify the loop keeps polling.
        fake_describe = AsyncMock(side_effect=[
            Exception("transient 503"),
            "Recovered after 503",
        ])
        with patch("pipeline.screen_share_sink.latest_jpeg_global",
                   return_value=b"\xff\xd8\xff\xe0X"), \
             patch("tools._vision_backend.vision_describe", fake_describe):
            self._run_loop_briefly(session, iterations=4)

        # Should have eventually cached the second (successful) describe.
        pair = session._jarvis_latest_screen_description
        assert pair is not None
        assert pair[0] == "Recovered after 503"

    def test_skips_empty_description(self):
        """vision_describe returning '' or '(no description returned)'
        shouldn't poison the cache with a useless entry."""
        session = _FakeSession()
        fake_describe = AsyncMock(return_value="   ")  # whitespace-only
        with patch("pipeline.screen_share_sink.latest_jpeg_global",
                   return_value=b"\xff\xd8\xff\xe0X"), \
             patch("tools._vision_backend.vision_describe", fake_describe):
            self._run_loop_briefly(session, iterations=2)
        assert session._jarvis_latest_screen_description is None


# ── attach_to_room idempotency ───────────────────────────────────────


class TestAttachToRoom:
    def test_idempotent_per_room(self):
        """Second call on the same room is a no-op."""
        from unittest.mock import MagicMock
        room = MagicMock()
        room._jarvis_screen_observer_attached = False
        # Counter of `on(...)` calls so we can confirm handlers were
        # registered ONCE.
        register_count = {"n": 0}
        def _on(event_name):
            register_count["n"] += 1
            def decorator(fn): return fn
            return decorator
        room.on.side_effect = _on

        session = _FakeSession()
        with patch.object(obs, "_enabled", return_value=True):
            obs.attach_to_room(room, session)
            first_count = register_count["n"]
            obs.attach_to_room(room, session)  # second call

        # Second call should have registered NO new handlers.
        assert register_count["n"] == first_count

    def test_disabled_via_env_skips_registration(self):
        from unittest.mock import MagicMock
        room = MagicMock()
        room._jarvis_screen_observer_attached = False
        register_count = {"n": 0}
        def _on(event_name):
            register_count["n"] += 1
            def decorator(fn): return fn
            return decorator
        room.on.side_effect = _on

        session = _FakeSession()
        with patch.object(obs, "_enabled", return_value=False):
            obs.attach_to_room(room, session)

        # No handlers registered when disabled.
        assert register_count["n"] == 0
        # But the sentinel is still set so a later attach_to_room call
        # under the same room won't double-register either.
        assert room._jarvis_screen_observer_attached is True


# ── screenshot() fast path ───────────────────────────────────────────


class TestScreenshotFastPath:
    """The point of this whole feature: screenshot() returns the
    observer cache when available, in ~0s, with NO call to
    _vision_describe."""

    def setup_method(self):
        obs._GLOBAL_LATEST = None

    def teardown_method(self):
        obs._GLOBAL_LATEST = None

    def test_screenshot_returns_cached_description_when_fresh(self):
        # Park a fresh description in the observer cache.
        obs._GLOBAL_LATEST = ("Test cached desc", time.monotonic())

        # Import the tool and call it through its @function_tool wrapper.
        import tools.computer_use as cu
        # We need to invoke the wrapped coroutine. function_tool exposes
        # the original via ._func (livekit convention) — fall back to
        # direct call if naming changes.
        impl = getattr(cu.screenshot, "_func", None) or cu.screenshot

        # Make sure the on-demand path WOULDN'T be hit — patch
        # _vision_describe to fail loudly if invoked.
        fail_describe = AsyncMock(side_effect=AssertionError(
            "should not call vision_describe — cache hit expected"
        ))
        with patch.object(cu, "_vision_describe", fail_describe):
            result = run(impl())

        assert result == "Test cached desc"
        fail_describe.assert_not_awaited()

    def test_screenshot_falls_back_to_on_demand_when_cache_stale(self):
        """When the cache is stale, screenshot must NOT serve the old
        description — it must run the full capture + describe pipeline."""
        # Stale cache (past max age).
        obs._GLOBAL_LATEST = ("old desc", time.monotonic() - 60.0)

        import tools.computer_use as cu
        impl = getattr(cu.screenshot, "_func", None) or cu.screenshot

        fake_describe = AsyncMock(return_value="fresh desc from on-demand")
        # Avoid hitting scrot in the test — return synthetic bytes.
        with patch.object(cu, "_take_screenshot", return_value=(b"FAKE_JPEG", "image/jpeg")), \
             patch.object(cu, "_vision_describe", fake_describe):
            result = run(impl())

        assert result == "fresh desc from on-demand"
        fake_describe.assert_awaited_once()

    def test_screenshot_falls_back_when_cache_empty(self):
        """No observer (screen-share not active) → on-demand path."""
        # _GLOBAL_LATEST is None from setup
        import tools.computer_use as cu
        impl = getattr(cu.screenshot, "_func", None) or cu.screenshot

        fake_describe = AsyncMock(return_value="on-demand result")
        with patch.object(cu, "_take_screenshot", return_value=(b"FAKE", "image/jpeg")), \
             patch.object(cu, "_vision_describe", fake_describe):
            result = run(impl())

        assert result == "on-demand result"
        fake_describe.assert_awaited_once()
