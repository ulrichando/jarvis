"""Default keybindings for JARVIS."""

from __future__ import annotations

from .types import KeybindingBlock

DEFAULT_BINDINGS: list[KeybindingBlock] = [
    KeybindingBlock(context="Global", bindings={
        "ctrl+c": "app:interrupt",
        "ctrl+d": "app:exit",
        "ctrl+l": "app:redraw",
        "ctrl+t": "app:toggleTodos",
        "ctrl+o": "app:toggleTranscript",
        "ctrl+r": "history:search",
        "escape": "app:cancel",
    }),
    KeybindingBlock(context="Chat", bindings={
        "enter": "chat:submit",
        "shift+enter": "chat:newline",
        "up": "chat:historyPrev",
        "down": "chat:historyNext",
        "tab": "chat:autocomplete",
        "ctrl+a": "edit:selectAll",
        "ctrl+e": "edit:endOfLine",
    }),
    KeybindingBlock(context="Confirmation", bindings={
        "y": "confirm:yes",
        "n": "confirm:no",
        "a": "confirm:always",
        "escape": "confirm:cancel",
    }),
    KeybindingBlock(context="Autocomplete", bindings={
        "tab": "autocomplete:accept",
        "up": "autocomplete:prev",
        "down": "autocomplete:next",
        "escape": "autocomplete:dismiss",
    }),
]
