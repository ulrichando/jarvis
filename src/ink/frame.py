"""Frame types and utilities for rendering."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

FlickerReason = Literal["resize", "offscreen", "clear"]


@dataclass
class Cursor:
    x: int = 0
    y: int = 0
    visible: bool = True


@dataclass
class Size:
    width: int = 0
    height: int = 0


@dataclass
class ScrollHint:
    top: int = 0
    bottom: int = 0
    delta: int = 0


@dataclass
class Frame:
    screen: Any = None
    viewport: Size = field(default_factory=Size)
    cursor: Cursor = field(default_factory=Cursor)
    scroll_hint: ScrollHint | None = None
    scroll_drain_pending: bool = False


def empty_frame(rows: int, columns: int, style_pool: Any = None, char_pool: Any = None, hyperlink_pool: Any = None) -> Frame:
    """Create an empty frame."""
    return Frame(
        screen=None,
        viewport=Size(width=columns, height=rows),
        cursor=Cursor(x=0, y=0, visible=True),
    )


@dataclass
class FrameEvent:
    duration_ms: float = 0.0
    phases: dict[str, Any] | None = None
    flickers: list[dict[str, Any]] = field(default_factory=list)


# Patch types
@dataclass
class Patch:
    type: str = ""
    content: str = ""
    count: int = 0
    x: int = 0
    y: int = 0
    col: int = 0
    reason: str = ""
    uri: str = ""
    str_: str = ""
    debug: dict[str, Any] | None = None

Diff = list[Patch]


def should_clear_screen(prev_frame: Frame, frame: Frame) -> FlickerReason | None:
    """Determine whether the screen should be cleared."""
    did_resize = (
        frame.viewport.height != prev_frame.viewport.height
        or frame.viewport.width != prev_frame.viewport.width
    )
    if did_resize:
        return "resize"

    current_overflows = frame.screen and frame.screen.height >= frame.viewport.height if frame.screen else False
    previous_overflowed = prev_frame.screen and prev_frame.screen.height >= prev_frame.viewport.height if prev_frame.screen else False
    if current_overflows or previous_overflowed:
        return "offscreen"

    return None
