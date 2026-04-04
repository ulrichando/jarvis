"""Track IDE at-mention notifications."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class IDEAtMentioned:
    file_path: str
    line_start: Optional[int] = None
    line_end: Optional[int] = None


class IdeAtMentionedTracker:
    """Tracks IDE at-mention notifications via MCP client notification handlers.

    Equivalent to useIdeAtMentioned React hook.
    """

    def __init__(self, on_at_mentioned: Callable[[IDEAtMentioned], None]):
        self._on_at_mentioned = on_at_mentioned

    def handle_notification(self, data: dict) -> None:
        params = data.get("params", {})
        file_path = params.get("filePath", "")
        line_start = params.get("lineStart")
        line_end = params.get("lineEnd")

        # Adjust to 1-based
        if line_start is not None:
            line_start += 1
        if line_end is not None:
            line_end += 1

        self._on_at_mentioned(IDEAtMentioned(
            file_path=file_path,
            line_start=line_start,
            line_end=line_end,
        ))
