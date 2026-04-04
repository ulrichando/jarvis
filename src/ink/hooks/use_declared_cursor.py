"""useDeclaredCursor hook - declare cursor position."""
from __future__ import annotations
from ..components.cursor_declaration_context import CursorDeclaration


class UseDeclaredCursor:
    """Declares a cursor position for the Ink renderer."""

    def __init__(self) -> None:
        self.declaration: CursorDeclaration | None = None

    def declare(self, x: int, y: int, visible: bool = True, shape: str = "block") -> None:
        self.declaration = CursorDeclaration(x=x, y=y, visible=visible, shape=shape)

    def clear(self) -> None:
        self.declaration = None
