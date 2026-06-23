"""Process-global experience signal for the cognitive evolution loop.

Producers (the telemetry turn-writer, the memory tool, the error logger) call
bump() when something evolution-worthy happens — a bug, a correction, a new
fact. The in-process _automod_loop awaits wait(), so evolution reacts to lived
experience instead of a fixed clock.

Cross-thread-safe: bump() may be called from any thread (telemetry/tool code
need not run on the agent's event loop). wait() blocks off-loop via
asyncio.to_thread on a threading.Event, so no event-loop reference is needed.

Lives under pipeline/automod/ (auto-mod HARD BLOCKLIST) — human-edited only.

Spec: docs/superpowers/specs/2026-06-23-cognitive-evolution-loop-design.md
"""
from __future__ import annotations

import asyncio
import collections
import threading

_event = threading.Event()
_reasons: collections.deque[str] = collections.deque(maxlen=50)
_lock = threading.Lock()


def bump(reason: str) -> None:
    """Record a reason + wake the loop. Safe from any thread. Never raises."""
    try:
        with _lock:
            _reasons.append(str(reason))
        _event.set()
    except Exception:
        pass


def drain_reasons() -> list[str]:
    """Return + clear the recorded reasons (consumed by the loop / reflection)."""
    with _lock:
        out = list(_reasons)
        _reasons.clear()
    return out


def is_set() -> bool:
    return _event.is_set()


def clear() -> None:
    _event.clear()


async def wait(timeout: float) -> bool:
    """Block off the event loop until bumped or `timeout` seconds elapse.
    Returns True if bumped, False on timeout."""
    return await asyncio.to_thread(_event.wait, timeout)
