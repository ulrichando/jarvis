"""Tests for reconnect_control — the pure direct-mode reconnect circuit-breaker.

Pure helper: classify() maps drops to transient/hard; ReconnectController
grows + caps + resets backoff and trips a storm cap; env overrides parse with
safe fallbacks.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from reconnect_control import ReconnectController, classify  # noqa: E402


class _FakeRcvd:
    def __init__(self, code):
        self.code = code


class _FakeWsClose(Exception):
    """Stand-in for a websockets ConnectionClosed with an .rcvd close frame."""

    def __init__(self, code, message=""):
        super().__init__(message)
        self.rcvd = _FakeRcvd(code)


class _FakeCodeExc(Exception):
    """Exception that exposes a top-level .code attribute (some libs do this)."""

    def __init__(self, code, message=""):
        super().__init__(message)
        self.code = code


def test_classify_transient():
    assert classify(_FakeWsClose(1011)) == "transient"
    assert classify(
        RuntimeError("sent 1011 (internal error) keepalive ping timeout")
    ) == "transient"
    assert classify(ConnectionError("x")) == "transient"


def test_classify_hard():
    assert classify(RuntimeError("RESOURCE_EXHAUSTED: quota exceeded")) == "hard"
    assert classify(RuntimeError("429 Too Many Requests")) == "hard"
    assert classify(RuntimeError("401 invalid api key")) == "hard"
    assert classify(_FakeCodeExc(1008)) == "hard"


def test_backoff_grows_caps_resets():
    rc = ReconnectController(backoff_floor=0.5, backoff_cap=5.0, stable_s=30.0)
    exc = ConnectionError("transient")
    delays = [rc.on_drop(0.0, exc).delay for _ in range(6)]
    assert delays == [0.5, 1.0, 2.0, 4.0, 5.0, 5.0]
    # A session that stayed up past stable_s resets the backoff to the floor.
    rc.mark_connected(100.0)
    d = rc.on_drop(131.0, exc)
    assert d.delay == 0.5


def test_storm_cap_trips():
    rc = ReconnectController(max_per_window=3, window_s=120.0)
    exc = ConnectionError("transient")
    assert rc.on_drop(0.0, exc).retry is True
    assert rc.on_drop(1.0, exc).retry is True
    assert rc.on_drop(2.0, exc).retry is True
    fourth = rc.on_drop(3.0, exc)
    assert fourth.retry is False
    assert "storm" in fourth.reason
    # Spread beyond the window: old events evicted → still retries.
    rc2 = ReconnectController(max_per_window=3, window_s=120.0)
    assert rc2.on_drop(0.0, exc).retry is True
    assert rc2.on_drop(200.0, exc).retry is True


def test_hard_never_retries():
    rc = ReconnectController()
    hard = RuntimeError("RESOURCE_EXHAUSTED: quota exceeded")
    for _ in range(10):
        d = rc.on_drop(0.0, hard)
        assert d.retry is False
        assert d.n == 0


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("JARVIS_RECONNECT_MAX_PER_WINDOW", "2")
    rc = ReconnectController()
    assert rc.max_per_window == 2
    # Bad value falls back to the default.
    monkeypatch.setenv("JARVIS_RECONNECT_MAX_PER_WINDOW", "abc")
    rc2 = ReconnectController()
    assert rc2.max_per_window == 6
