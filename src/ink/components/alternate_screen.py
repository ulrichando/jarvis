"""AlternateScreen component - switches to terminal alternate screen buffer."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AlternateScreenProps:
    """Properties for the AlternateScreen component."""
    enter_message: str = ""
    exit_message: str = ""


class AlternateScreen:
    """Switches to the terminal's alternate screen buffer.

    Content rendered inside is drawn on a clean screen.
    On unmount, the original screen is restored.
    """

    def __init__(self, props: AlternateScreenProps | None = None) -> None:
        self.props = props or AlternateScreenProps()
        self._in_alt_screen = False

    def enter(self) -> str:
        """Return escape sequence to enter alternate screen."""
        from ..termio.dec import ENTER_ALT_SCREEN
        self._in_alt_screen = True
        return ENTER_ALT_SCREEN

    def exit(self) -> str:
        """Return escape sequence to exit alternate screen."""
        from ..termio.dec import EXIT_ALT_SCREEN
        self._in_alt_screen = False
        return EXIT_ALT_SCREEN
