"""Cursor declaration context."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class CursorDeclaration:
    """A declared cursor position."""
    x: int = 0
    y: int = 0
    visible: bool = True
    shape: str = "block"  # 'block' | 'underline' | 'bar'


@dataclass
class CursorDeclarationContext:
    """Context for cursor position declarations."""
    declare: Callable[[CursorDeclaration], Callable] | None = None
