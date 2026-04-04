"""
Session activity tracking with refcount-based heartbeat timer.

Callers bracket their work with start_session_activity() / stop_session_activity().
When the refcount is > 0, a periodic timer fires the registered callback
to keep the session alive.
"""

from __future__ import annotations

import asyncio
import time
from typing import Callable, Dict, Literal, Optional

SESSION_ACTIVITY_INTERVAL_S = 30.0

SessionActivityReason = Literal["api_call", "tool_exec"]

_activity_callback: Optional[Callable[[], None]] = None
_refcount: int = 0
_active_reasons: Dict[SessionActivityReason, int] = {}
_oldest_activity_started_at: Optional[float] = None
_heartbeat_task: Optional[asyncio.TimerHandle] = None
_idle_task: Optional[asyncio.TimerHandle] = None


def register_session_activity_callback(cb: Callable[[], None]) -> None:
    """Register a callback to be called periodically during activity."""
    global _activity_callback
    _activity_callback = cb


def unregister_session_activity_callback() -> None:
    """Remove the activity callback and stop all timers."""
    global _activity_callback, _heartbeat_task, _idle_task
    _activity_callback = None
    if _heartbeat_task is not None:
        _heartbeat_task.cancel()
        _heartbeat_task = None
    if _idle_task is not None:
        _idle_task.cancel()
        _idle_task = None


def is_session_activity_tracking_active() -> bool:
    """Check if activity tracking is active."""
    return _activity_callback is not None


def start_session_activity(reason: SessionActivityReason) -> None:
    """
    Increment the activity refcount.
    When it transitions from 0 -> 1, log the start.
    """
    global _refcount, _oldest_activity_started_at
    _refcount += 1
    _active_reasons[reason] = _active_reasons.get(reason, 0) + 1

    if _refcount == 1:
        _oldest_activity_started_at = time.monotonic()


def stop_session_activity(reason: SessionActivityReason) -> None:
    """
    Decrement the activity refcount.
    When it reaches 0, log the idle state.
    """
    global _refcount
    if _refcount > 0:
        _refcount -= 1

    count = _active_reasons.get(reason, 0) - 1
    if count > 0:
        _active_reasons[reason] = count
    else:
        _active_reasons.pop(reason, None)


def get_activity_info() -> Dict:
    """Return current activity state for diagnostics."""
    return {
        "refcount": _refcount,
        "active_reasons": dict(_active_reasons),
        "oldest_activity_ms": (
            int((time.monotonic() - _oldest_activity_started_at) * 1000)
            if _refcount > 0 and _oldest_activity_started_at is not None
            else None
        ),
    }
