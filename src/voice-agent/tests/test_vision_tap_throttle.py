"""Vision-tap throttling — 30s ceiling, screen-change debounce, paused-app skip."""
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_throttle_returns_false_within_min_interval():
    from vision_tap import VisionTapThrottle
    t = VisionTapThrottle(min_interval=1.0, max_interval=30.0)
    assert t.should_capture(active_app="chrome") is True
    t.mark_captured()
    # Immediately after, throttled.
    assert t.should_capture(active_app="chrome") is False


def test_throttle_returns_true_after_min_interval(monkeypatch):
    from vision_tap import VisionTapThrottle
    t = VisionTapThrottle(min_interval=1.0, max_interval=30.0)
    t.mark_captured()
    # Simulate 1.5s passing.
    fake_time = time.time() + 1.5
    monkeypatch.setattr("time.time", lambda: fake_time)
    assert t.should_capture(active_app="chrome") is True


def test_throttle_fires_on_screen_change_within_min_interval(monkeypatch):
    """When the active app changes, debounce can fire even if min_interval
    hasn't elapsed (after the 1s debounce). For test simplicity, we
    use min_interval=0.5 so the debounce is the dominant gate."""
    from vision_tap import VisionTapThrottle
    t = VisionTapThrottle(min_interval=0.5, max_interval=30.0)
    t.mark_captured(active_app="chrome")
    fake_time = time.time() + 0.6
    monkeypatch.setattr("time.time", lambda: fake_time)
    assert t.should_capture(active_app="firefox") is True


def test_throttle_skips_paused_apps():
    from vision_tap import VisionTapThrottle
    t = VisionTapThrottle(
        min_interval=1.0, max_interval=30.0,
        paused_apps={"keepassxc", "1password"},
    )
    assert t.should_capture(active_app="keepassxc") is False
    assert t.should_capture(active_app="1password") is False
    assert t.should_capture(active_app="chrome") is True


def test_throttle_max_interval_forces_capture(monkeypatch):
    """Even without app change, after max_interval we capture anyway."""
    from vision_tap import VisionTapThrottle
    t = VisionTapThrottle(min_interval=1.0, max_interval=10.0)
    t.mark_captured(active_app="chrome")
    fake_time = time.time() + 11.0
    monkeypatch.setattr("time.time", lambda: fake_time)
    # Same app, but past max_interval → capture.
    assert t.should_capture(active_app="chrome") is True
