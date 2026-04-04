"""Event handler props and reverse lookup tables."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional


@dataclass
class EventHandlerProps:
    """Props for event handlers on Box and other host components."""
    on_key_down: Callable | None = None
    on_key_down_capture: Callable | None = None
    on_focus: Callable | None = None
    on_focus_capture: Callable | None = None
    on_blur: Callable | None = None
    on_blur_capture: Callable | None = None
    on_paste: Callable | None = None
    on_paste_capture: Callable | None = None
    on_resize: Callable | None = None
    on_click: Callable | None = None
    on_mouse_enter: Callable | None = None
    on_mouse_leave: Callable | None = None


@dataclass
class HandlerMapping:
    """Maps event type to handler prop names."""
    bubble: str | None = None
    capture: str | None = None


HANDLER_FOR_EVENT: dict[str, HandlerMapping] = {
    "keydown": HandlerMapping(bubble="on_key_down", capture="on_key_down_capture"),
    "focus": HandlerMapping(bubble="on_focus", capture="on_focus_capture"),
    "blur": HandlerMapping(bubble="on_blur", capture="on_blur_capture"),
    "paste": HandlerMapping(bubble="on_paste", capture="on_paste_capture"),
    "resize": HandlerMapping(bubble="on_resize"),
    "click": HandlerMapping(bubble="on_click"),
}

EVENT_HANDLER_PROPS: set[str] = {
    "on_key_down",
    "on_key_down_capture",
    "on_focus",
    "on_focus_capture",
    "on_blur",
    "on_blur_capture",
    "on_paste",
    "on_paste_capture",
    "on_resize",
    "on_click",
    "on_mouse_enter",
    "on_mouse_leave",
}
