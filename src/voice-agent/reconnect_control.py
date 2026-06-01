"""Provider-agnostic reconnect circuit-breaker for the direct voice modes.
Pure: no I/O, no import-time side effects. Decides whether a dropped Live/
Realtime WebSocket should reconnect IN-PROCESS, how long to back off, and
when to give up (caller then reverts to JARVIS-Claude)."""
from __future__ import annotations
import os
from dataclasses import dataclass, field


def _envf(name: str, default: float) -> float:
    try:
        v = float(os.environ.get(name, str(default)))
        return v if v > 0 else default
    except (TypeError, ValueError):
        return default


def _envi(name: str, default: int) -> int:
    try:
        v = int(float(os.environ.get(name, str(default))))
        return v if v > 0 else default
    except (TypeError, ValueError):
        return default


# Substrings (lowercased) in the exception's "Type: message" that mean HARD (don't retry).
_HARD_MARKERS = ("quota", "resource_exhausted", "insufficient_quota", "exceeded",
                 "permission", "unauthorized", "api key", "invalid_api_key",
                 "401", "403", "429", "1008")
# websockets close codes treated as TRANSIENT (reconnect in-process).
_TRANSIENT_CLOSE_CODES = {1000, 1001, 1006, 1011, 1012, 1013}


def classify(exc) -> str:
    """'hard' (give up -> revert to Claude) or 'transient' (reconnect in-process)."""
    msg = f"{type(exc).__name__}: {exc}".lower()
    if any(m in msg for m in _HARD_MARKERS):
        return "hard"
    code = getattr(exc, "code", None)
    if code is None:
        code = getattr(getattr(exc, "rcvd", None), "code", None)   # websockets Close frame
    if isinstance(code, int):
        if code == 1008:
            return "hard"
        if code in _TRANSIENT_CLOSE_CODES:
            return "transient"
    return "transient"   # ConnectionClosed/keepalive/timeout/unknown -> retry (storm cap bounds it)


@dataclass
class Decision:
    retry: bool
    delay: float
    n: int
    reason: str


@dataclass
class ReconnectController:
    backoff_floor: float = field(default_factory=lambda: _envf("JARVIS_RECONNECT_BACKOFF_FLOOR_S", 0.5))
    backoff_cap: float   = field(default_factory=lambda: _envf("JARVIS_RECONNECT_BACKOFF_CAP_S", 5.0))
    stable_s: float      = field(default_factory=lambda: _envf("JARVIS_RECONNECT_STABLE_S", 30.0))
    max_per_window: int  = field(default_factory=lambda: _envi("JARVIS_RECONNECT_MAX_PER_WINDOW", 6))
    window_s: float      = field(default_factory=lambda: _envf("JARVIS_RECONNECT_WINDOW_S", 120.0))
    _events: list = field(default_factory=list)
    _connected_at: "float | None" = None
    _cur_backoff: "float | None" = None

    def mark_connected(self, now: float) -> None:
        self._connected_at = now

    def on_drop(self, now: float, exc) -> Decision:
        # Stable-session reset: a long-lived session that finally drops reconnects fast.
        if self._connected_at is not None and (now - self._connected_at) >= self.stable_s:
            self._cur_backoff = None
        self._connected_at = None
        if classify(exc) == "hard":
            return Decision(False, 0.0, 0, f"hard failure ({type(exc).__name__})")
        self._events.append(now)
        self._events = [t for t in self._events if now - t <= self.window_s]
        n = len(self._events)
        if n > self.max_per_window:
            return Decision(False, 0.0, n, f"reconnect storm ({n} in {self.window_s:.0f}s)")
        self._cur_backoff = (self.backoff_floor if self._cur_backoff is None
                             else min(self.backoff_cap, self._cur_backoff * 2))
        return Decision(True, self._cur_backoff, n, "transient")
