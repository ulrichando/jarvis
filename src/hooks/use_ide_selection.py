"""Track IDE text selection changes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class SelectionPoint:
    line: int
    character: int


@dataclass
class IDESelection:
    line_count: int = 0
    line_start: Optional[int] = None
    text: Optional[str] = None
    file_path: Optional[str] = None


class IdeSelectionTracker:
    """Tracks IDE text selection information via MCP notification handlers.

    Equivalent to useIdeSelection React hook.
    """

    def __init__(self, on_select: Callable[[IDESelection], None]):
        self._on_select = on_select

    def handle_notification(self, data: dict) -> None:
        params = data.get("params", {})
        selection = params.get("selection")
        text = params.get("text")
        file_path = params.get("filePath")

        if selection and selection.get("start") and selection.get("end"):
            start = selection["start"]
            end = selection["end"]
            line_count = end["line"] - start["line"] + 1
            if end.get("character", 0) == 0:
                line_count -= 1

            self._on_select(IDESelection(
                line_count=line_count,
                line_start=start["line"],
                text=text,
                file_path=file_path,
            ))
        elif text is not None:
            self._on_select(IDESelection(
                line_count=0,
                text=text,
                file_path=file_path,
            ))
