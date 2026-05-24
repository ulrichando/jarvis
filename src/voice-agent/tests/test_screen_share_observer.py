"""Tests for pipeline.screen_share_observer.

Tests cover:
  - latest_description / latest_description_global freshness gating
  - attach_to_room registers handlers idempotently

Note: TestPollLoop and TestScreenshotFastPath removed — they depended on
tools._vision_backend and tools.computer_use which were removed in the
Hermes teardown.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline import screen_share_observer as obs


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


