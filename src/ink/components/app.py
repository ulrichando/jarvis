"""App component - the root component of an Ink application."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class AppProps:
    """Properties for the App component."""
    exit_on_ctrl_c: bool = True
    on_exit: Callable[[Exception | None], None] | None = None


class App:
    """Root component that provides context to the Ink tree.

    Manages stdin handling, raw mode, and exit behavior.
    In the TS version this is a React component; here it's a plain class
    with the same lifecycle logic.
    """

    def __init__(self, props: AppProps | None = None) -> None:
        self.props = props or AppProps()
        self._exit_error: Exception | None = None

    def exit(self, error: Exception | None = None) -> None:
        """Exit the application."""
        self._exit_error = error
        if self.props.on_exit:
            self.props.on_exit(error)
