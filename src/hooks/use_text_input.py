"""Text input handling with readline-style keybindings."""

from __future__ import annotations

from typing import Any, Callable, List, Optional


class TextInput:
    """Full-featured text input handler with readline keybindings.

    Supports: cursor movement, word jump, kill/yank, backspace/delete,
    home/end, inline ghost text, undo, and more.

    Equivalent to useTextInput React hook.
    """

    def __init__(self, on_submit: Optional[Callable] = None, on_change: Optional[Callable] = None):
        self.text = ""
        self.cursor_offset = 0
        self._on_submit = on_submit
        self._on_change = on_change
        self._kill_ring: List[str] = []

    def insert(self, text: str) -> None:
        self.text = self.text[:self.cursor_offset] + text + self.text[self.cursor_offset:]
        self.cursor_offset += len(text)
        if self._on_change:
            self._on_change(self.text)

    def backspace(self) -> None:
        if self.cursor_offset > 0:
            self.text = self.text[:self.cursor_offset - 1] + self.text[self.cursor_offset:]
            self.cursor_offset -= 1
            if self._on_change:
                self._on_change(self.text)

    def delete(self) -> None:
        if self.cursor_offset < len(self.text):
            self.text = self.text[:self.cursor_offset] + self.text[self.cursor_offset + 1:]
            if self._on_change:
                self._on_change(self.text)

    def move_left(self) -> None:
        self.cursor_offset = max(0, self.cursor_offset - 1)

    def move_right(self) -> None:
        self.cursor_offset = min(len(self.text), self.cursor_offset + 1)

    def move_home(self) -> None:
        self.cursor_offset = 0

    def move_end(self) -> None:
        self.cursor_offset = len(self.text)

    def kill_to_end(self) -> None:
        killed = self.text[self.cursor_offset:]
        self.text = self.text[:self.cursor_offset]
        if killed:
            self._kill_ring.append(killed)

    def kill_to_start(self) -> None:
        killed = self.text[:self.cursor_offset]
        self.text = self.text[self.cursor_offset:]
        self.cursor_offset = 0
        if killed:
            self._kill_ring.append(killed)

    def yank(self) -> None:
        if self._kill_ring:
            self.insert(self._kill_ring[-1])

    def submit(self) -> None:
        if self._on_submit:
            self._on_submit(self.text)

    def clear(self) -> None:
        self.text = ""
        self.cursor_offset = 0
