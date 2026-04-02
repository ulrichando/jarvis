"""JARVIS SSE Parser — incremental Server-Sent Events stream parser.

Ported from claw-code's sse.rs. Handles chunked SSE responses from
LLM APIs (Anthropic, OpenAI) with support for:
- Multi-line data fields
- Event type filtering
- Ping/keep-alive handling
- [DONE] marker detection
"""

import json
import logging
from dataclasses import dataclass, field

log = logging.getLogger("jarvis.sse")


@dataclass
class SseEvent:
    """A parsed SSE event."""
    event: str = ""      # Event type (e.g., "message_start", "content_block_delta")
    data: str = ""       # Raw data string
    id: str = ""         # Event ID (optional)
    retry: int = 0       # Retry interval (optional)
    parsed: dict = field(default_factory=dict)  # Parsed JSON data


class SseParser:
    """Incremental SSE stream parser.

    Feed chunks of bytes/text via push(), get back complete events.
    Call finish() when stream ends to flush any remaining data.
    """

    def __init__(self):
        self._buffer = ""
        self._event_name = ""
        self._data_lines: list[str] = []
        self._event_id = ""
        self._retry = 0

    def push(self, chunk: str | bytes) -> list[SseEvent]:
        """Process a chunk of SSE data. Returns list of complete events."""
        if isinstance(chunk, bytes):
            chunk = chunk.decode("utf-8", errors="replace")

        self._buffer += chunk
        events = []

        # Process complete lines
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            line = line.rstrip("\r")  # Handle \r\n

            event = self._process_line(line)
            if event is not None:
                events.append(event)

        return events

    def finish(self) -> list[SseEvent]:
        """Flush remaining buffer. Call when stream ends."""
        events = []
        if self._buffer.strip():
            event = self._process_line(self._buffer.strip())
            if event is not None:
                events.append(event)
            self._buffer = ""

        # Flush any pending data
        if self._data_lines:
            event = self._take_event()
            if event is not None:
                events.append(event)

        return events

    def _process_line(self, line: str) -> SseEvent | None:
        """Process a single SSE line. Returns event if complete (empty line)."""
        # Empty line = event boundary
        if not line:
            return self._take_event()

        # Comment line (starts with :)
        if line.startswith(":"):
            return None

        # Field:value parsing
        if ":" in line:
            field_name, _, value = line.partition(":")
            value = value.lstrip(" ")  # Strip single leading space
        else:
            field_name = line
            value = ""

        if field_name == "event":
            self._event_name = value
        elif field_name == "data":
            self._data_lines.append(value)
        elif field_name == "id":
            self._event_id = value
        elif field_name == "retry":
            try:
                self._retry = int(value)
            except ValueError:
                pass

        return None

    def _take_event(self) -> SseEvent | None:
        """Finalize and return the current event, if any."""
        if not self._data_lines:
            self._reset()
            return None

        data = "\n".join(self._data_lines)

        # Skip ping events
        if self._event_name == "ping":
            self._reset()
            return None

        # Skip [DONE] marker
        if data.strip() == "[DONE]":
            self._reset()
            return None

        # Try to parse JSON
        parsed = {}
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError:
            pass

        event = SseEvent(
            event=self._event_name,
            data=data,
            id=self._event_id,
            retry=self._retry,
            parsed=parsed,
        )

        self._reset()
        return event

    def _reset(self):
        """Reset state for next event."""
        self._event_name = ""
        self._data_lines = []
        self._event_id = ""
        self._retry = 0
