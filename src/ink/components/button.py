"""Button component."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class ButtonProps:
    label: str = ""
    on_click: Callable | None = None
    disabled: bool = False


class Button:
    """A clickable button component."""

    def __init__(self, props: ButtonProps | None = None) -> None:
        self.props = props or ButtonProps()

    def click(self) -> None:
        if not self.props.disabled and self.props.on_click:
            self.props.on_click()
