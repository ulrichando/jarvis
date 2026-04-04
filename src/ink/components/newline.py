"""Newline component."""

from __future__ import annotations


class Newline:
    """Renders a newline character."""

    def __init__(self, count: int = 1) -> None:
        self.count = count

    def render(self) -> str:
        return "\n" * self.count
