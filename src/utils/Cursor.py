"""Cursor/kill ring utilities for text input handling."""

from __future__ import annotations

from typing import Optional

KILL_RING_MAX_SIZE = 10
_kill_ring: list[str] = []
_kill_ring_index = 0
_last_action_was_kill = False
_last_yank_start = 0
_last_yank_length = 0
_last_action_was_yank = False


def push_to_kill_ring(text: str, direction: str = "append") -> None:
    """Push text to the kill ring."""
    global _last_action_was_kill
    if not text:
        return

    if _last_action_was_kill and _kill_ring:
        if direction == "prepend":
            _kill_ring[0] = text + _kill_ring[0]
        else:
            _kill_ring[0] = _kill_ring[0] + text
    else:
        _kill_ring.insert(0, text)
        if len(_kill_ring) > KILL_RING_MAX_SIZE:
            _kill_ring.pop()

    _last_action_was_kill = True


def get_last_kill() -> str:
    return _kill_ring[0] if _kill_ring else ""


def get_kill_ring_item(index: int) -> str:
    if not _kill_ring:
        return ""
    normalized = index % len(_kill_ring)
    return _kill_ring[normalized]


def get_kill_ring_size() -> int:
    return len(_kill_ring)


def clear_kill_ring() -> None:
    global _kill_ring_index, _last_action_was_kill, _last_action_was_yank
    global _last_yank_start, _last_yank_length
    _kill_ring.clear()
    _kill_ring_index = 0
    _last_action_was_kill = False
    _last_action_was_yank = False
    _last_yank_start = 0
    _last_yank_length = 0


def reset_kill_accumulation() -> None:
    global _last_action_was_kill
    _last_action_was_kill = False


def record_yank(start: int, length: int) -> None:
    global _last_yank_start, _last_yank_length, _last_action_was_yank
    _last_yank_start = start
    _last_yank_length = length
    _last_action_was_yank = True
