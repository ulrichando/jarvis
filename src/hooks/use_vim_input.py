"""Vim-style input handling layered on top of text input."""

from __future__ import annotations

from enum import Enum
from typing import Any, Callable, Optional

from .use_text_input import TextInput


class VimMode(Enum):
    NORMAL = "NORMAL"
    INSERT = "INSERT"
    VISUAL = "VISUAL"
    REPLACE = "REPLACE"


class VimInput:
    """Vim-style input with modal editing (NORMAL/INSERT/VISUAL/REPLACE).

    Wraps TextInput with vim keybindings and mode management.

    Equivalent to useVimInput React hook.
    """

    def __init__(
        self,
        on_submit: Optional[Callable] = None,
        on_mode_change: Optional[Callable] = None,
        on_undo: Optional[Callable] = None,
    ):
        self.text_input = TextInput(on_submit=on_submit)
        self.mode = VimMode.INSERT
        self._on_mode_change = on_mode_change
        self._on_undo = on_undo

    def set_mode(self, mode: VimMode) -> None:
        self.mode = mode
        if self._on_mode_change:
            self._on_mode_change(mode.value)

    def handle_key(self, key: str, ctrl: bool = False, meta: bool = False) -> None:
        if self.mode == VimMode.INSERT:
            self._handle_insert_mode(key, ctrl, meta)
        elif self.mode == VimMode.NORMAL:
            self._handle_normal_mode(key, ctrl, meta)

    def _handle_insert_mode(self, key: str, ctrl: bool, meta: bool) -> None:
        if key == "escape":
            self.set_mode(VimMode.NORMAL)
            return
        if ctrl and key == "c":
            self.set_mode(VimMode.NORMAL)
            return
        # Regular text input
        if len(key) == 1 and not ctrl and not meta:
            self.text_input.insert(key)

    def _handle_normal_mode(self, key: str, ctrl: bool, meta: bool) -> None:
        if key == "i":
            self.set_mode(VimMode.INSERT)
        elif key == "a":
            self.text_input.move_right()
            self.set_mode(VimMode.INSERT)
        elif key == "h":
            self.text_input.move_left()
        elif key == "l":
            self.text_input.move_right()
        elif key == "0":
            self.text_input.move_home()
        elif key == "$":
            self.text_input.move_end()
        elif key == "x":
            self.text_input.delete()
        elif key == "u" and self._on_undo:
            self._on_undo()
        elif key == "A":
            self.text_input.move_end()
            self.set_mode(VimMode.INSERT)
        elif key == "I":
            self.text_input.move_home()
            self.set_mode(VimMode.INSERT)
