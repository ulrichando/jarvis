"""Search input handler with readline-style keybindings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional, Set

UNHANDLED_SPECIAL_KEYS: Set[str] = {
    "pageup", "pagedown", "insert", "wheelup", "wheeldown", "mouse",
    "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12",
}


@dataclass
class SearchInputResult:
    query: str
    cursor_offset: int


class SearchInput:
    """Search input with readline-style editing keybindings.

    Supports: cursor movement, word jump, kill/yank, backspace/delete,
    home/end, and character insertion.

    Equivalent to useSearchInput React hook.
    """

    def __init__(
        self,
        on_exit: Callable[[], None],
        on_cancel: Optional[Callable[[], None]] = None,
        on_exit_up: Optional[Callable[[], None]] = None,
        passthrough_ctrl_keys: Optional[List[str]] = None,
        initial_query: str = "",
        backspace_exits_on_empty: bool = True,
    ):
        self.on_exit = on_exit
        self.on_cancel = on_cancel
        self.on_exit_up = on_exit_up
        self.passthrough_ctrl_keys = passthrough_ctrl_keys or []
        self.backspace_exits_on_empty = backspace_exits_on_empty
        self.query = initial_query
        self.cursor_offset = len(initial_query)
        self._kill_ring: List[str] = []

    def set_query(self, q: str) -> None:
        self.query = q
        self.cursor_offset = len(q)

    def handle_key(
        self,
        key: str,
        ctrl: bool = False,
        meta: bool = False,
        shift: bool = False,
        fn: bool = False,
    ) -> None:
        """Handle a key event."""
        # Check passthrough ctrl keys
        if ctrl and key.lower() in self.passthrough_ctrl_keys:
            return

        # Exit conditions
        if key in ("return", "down"):
            self.on_exit()
            return
        if key == "up":
            if self.on_exit_up:
                self.on_exit_up()
            return
        if key == "escape":
            if self.on_cancel:
                self.on_cancel()
            elif self.query:
                self.query = ""
                self.cursor_offset = 0
            else:
                self.on_exit()
            return

        # Backspace/Delete
        if key == "backspace":
            if meta:
                # Delete word before
                pos = self._find_word_boundary_before()
                deleted = self.query[pos:self.cursor_offset]
                self.query = self.query[:pos] + self.query[self.cursor_offset:]
                self.cursor_offset = pos
                if deleted:
                    self._kill_ring.append(deleted)
                return
            if not self.query:
                if self.backspace_exits_on_empty:
                    (self.on_cancel or self.on_exit)()
                return
            if self.cursor_offset > 0:
                self.query = (
                    self.query[:self.cursor_offset - 1]
                    + self.query[self.cursor_offset:]
                )
                self.cursor_offset -= 1
            return

        if key == "delete":
            if self.cursor_offset < len(self.query):
                self.query = (
                    self.query[:self.cursor_offset]
                    + self.query[self.cursor_offset + 1:]
                )
            return

        # Arrow keys with modifiers (word jump)
        if key == "left" and (ctrl or meta or fn):
            self.cursor_offset = self._find_word_boundary_before()
            return
        if key == "right" and (ctrl or meta or fn):
            self.cursor_offset = self._find_word_boundary_after()
            return

        # Plain arrow keys
        if key == "left":
            self.cursor_offset = max(0, self.cursor_offset - 1)
            return
        if key == "right":
            self.cursor_offset = min(len(self.query), self.cursor_offset + 1)
            return

        # Home/End
        if key == "home":
            self.cursor_offset = 0
            return
        if key == "end":
            self.cursor_offset = len(self.query)
            return

        # Ctrl key bindings
        if ctrl:
            k = key.lower()
            if k == "a":
                self.cursor_offset = 0
            elif k == "e":
                self.cursor_offset = len(self.query)
            elif k == "b":
                self.cursor_offset = max(0, self.cursor_offset - 1)
            elif k == "f":
                self.cursor_offset = min(len(self.query), self.cursor_offset + 1)
            elif k == "d":
                if not self.query:
                    (self.on_cancel or self.on_exit)()
                elif self.cursor_offset < len(self.query):
                    self.query = self.query[:self.cursor_offset] + self.query[self.cursor_offset + 1:]
            elif k == "h":
                if not self.query:
                    if self.backspace_exits_on_empty:
                        (self.on_cancel or self.on_exit)()
                elif self.cursor_offset > 0:
                    self.query = self.query[:self.cursor_offset - 1] + self.query[self.cursor_offset:]
                    self.cursor_offset -= 1
            elif k == "k":
                killed = self.query[self.cursor_offset:]
                self.query = self.query[:self.cursor_offset]
                if killed:
                    self._kill_ring.append(killed)
            elif k == "u":
                killed = self.query[:self.cursor_offset]
                self.query = self.query[self.cursor_offset:]
                self.cursor_offset = 0
                if killed:
                    self._kill_ring.append(killed)
            elif k == "w":
                pos = self._find_word_boundary_before()
                killed = self.query[pos:self.cursor_offset]
                self.query = self.query[:pos] + self.query[self.cursor_offset:]
                self.cursor_offset = pos
                if killed:
                    self._kill_ring.append(killed)
            elif k == "y":
                if self._kill_ring:
                    text = self._kill_ring[-1]
                    self.query = self.query[:self.cursor_offset] + text + self.query[self.cursor_offset:]
                    self.cursor_offset += len(text)
            elif k in ("g", "c"):
                if self.on_cancel:
                    self.on_cancel()
            return

        # Meta key bindings
        if meta:
            k = key.lower()
            if k == "b":
                self.cursor_offset = self._find_word_boundary_before()
            elif k == "f":
                self.cursor_offset = self._find_word_boundary_after()
            elif k == "d":
                pos = self._find_word_boundary_after()
                self.query = self.query[:self.cursor_offset] + self.query[pos:]
            return

        # Tab: ignore
        if key == "tab":
            return

        # Regular character input
        if len(key) >= 1 and key not in UNHANDLED_SPECIAL_KEYS:
            self.query = self.query[:self.cursor_offset] + key + self.query[self.cursor_offset:]
            self.cursor_offset += len(key)

    def _find_word_boundary_before(self) -> int:
        """Find the start of the previous word."""
        pos = self.cursor_offset - 1
        # Skip whitespace
        while pos > 0 and self.query[pos] == " ":
            pos -= 1
        # Skip word characters
        while pos > 0 and self.query[pos - 1] != " ":
            pos -= 1
        return max(0, pos)

    def _find_word_boundary_after(self) -> int:
        """Find the end of the next word."""
        pos = self.cursor_offset
        # Skip whitespace
        while pos < len(self.query) and self.query[pos] == " ":
            pos += 1
        # Skip word characters
        while pos < len(self.query) and self.query[pos] != " ":
            pos += 1
        return pos

    @property
    def result(self) -> SearchInputResult:
        return SearchInputResult(query=self.query, cursor_offset=self.cursor_offset)
