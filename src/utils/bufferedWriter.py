"""
Buffered writer for batching write operations.
"""

from __future__ import annotations

import threading
from typing import Callable, Optional


class BufferedWriter:
    """Buffers writes and flushes periodically or on overflow."""

    def __init__(
        self,
        write_fn: Callable[[str], None],
        flush_interval_ms: float = 1000,
        max_buffer_size: int = 100,
        max_buffer_bytes: float = float("inf"),
        immediate_mode: bool = False,
    ) -> None:
        self._write_fn = write_fn
        self._flush_interval_ms = flush_interval_ms
        self._max_buffer_size = max_buffer_size
        self._max_buffer_bytes = max_buffer_bytes
        self._immediate_mode = immediate_mode
        self._buffer: list[str] = []
        self._buffer_bytes = 0
        self._timer: Optional[threading.Timer] = None
        self._lock = threading.Lock()

    def _clear_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def flush(self) -> None:
        """Flush all buffered content."""
        with self._lock:
            if not self._buffer:
                self._clear_timer()
                return
            content = "".join(self._buffer)
            self._buffer = []
            self._buffer_bytes = 0
            self._clear_timer()
        self._write_fn(content)

    def _schedule_flush(self) -> None:
        if self._timer is None:
            self._timer = threading.Timer(
                self._flush_interval_ms / 1000, self.flush
            )
            self._timer.daemon = True
            self._timer.start()

    def write(self, content: str) -> None:
        """Write content, buffering if not in immediate mode."""
        if self._immediate_mode:
            self._write_fn(content)
            return

        with self._lock:
            self._buffer.append(content)
            self._buffer_bytes += len(content)
            self._schedule_flush()

            if (
                len(self._buffer) >= self._max_buffer_size
                or self._buffer_bytes >= self._max_buffer_bytes
            ):
                content = "".join(self._buffer)
                self._buffer = []
                self._buffer_bytes = 0
                self._clear_timer()

        if content:
            self._write_fn(content)

    def dispose(self) -> None:
        """Flush and clean up."""
        self.flush()


def create_buffered_writer(
    write_fn: Callable[[str], None],
    flush_interval_ms: float = 1000,
    max_buffer_size: int = 100,
    max_buffer_bytes: float = float("inf"),
    immediate_mode: bool = False,
) -> BufferedWriter:
    """Create a new buffered writer."""
    return BufferedWriter(
        write_fn=write_fn,
        flush_interval_ms=flush_interval_ms,
        max_buffer_size=max_buffer_size,
        max_buffer_bytes=max_buffer_bytes,
        immediate_mode=immediate_mode,
    )
