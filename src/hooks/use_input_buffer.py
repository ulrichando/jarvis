"""Input undo buffer with debounce."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class BufferEntry:
    text: str
    cursor_offset: int
    pasted_contents: Dict[int, dict] = field(default_factory=dict)
    timestamp: float = 0


class InputBuffer:
    """Input undo buffer with debounce.

    Equivalent to useInputBuffer React hook.
    """

    def __init__(self, max_buffer_size: int = 100, debounce_ms: int = 300):
        self.max_buffer_size = max_buffer_size
        self.debounce_ms = debounce_ms
        self._buffer: List[BufferEntry] = []
        self._current_index = -1
        self._last_push_time: float = 0

    def push(self, text: str, cursor_offset: int, pasted_contents: Optional[dict] = None) -> None:
        now = time.time() * 1000
        if now - self._last_push_time < self.debounce_ms:
            return
        self._last_push_time = now

        if self._current_index >= 0:
            self._buffer = self._buffer[: self._current_index + 1]

        if self._buffer and self._buffer[-1].text == text:
            return

        self._buffer.append(BufferEntry(
            text=text,
            cursor_offset=cursor_offset,
            pasted_contents=pasted_contents or {},
            timestamp=now,
        ))
        if len(self._buffer) > self.max_buffer_size:
            self._buffer = self._buffer[-self.max_buffer_size :]
        self._current_index = len(self._buffer) - 1

    def undo(self) -> Optional[BufferEntry]:
        if self._current_index < 1 or not self._buffer:
            return None
        self._current_index = max(0, self._current_index - 1)
        return self._buffer[self._current_index]

    @property
    def can_undo(self) -> bool:
        return self._current_index > 0 and len(self._buffer) > 1

    def clear(self) -> None:
        self._buffer.clear()
        self._current_index = -1
        self._last_push_time = 0
