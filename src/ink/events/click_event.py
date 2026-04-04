"""Mouse click event for terminal UI.

Fired on left-button release without drag, only when mouse tracking
is enabled (i.e. inside AlternateScreen).

Bubbles from the deepest hit node up through parentNode. Call
stop_immediate_propagation() to prevent ancestors' onClick from firing.
"""

from .event import Event


class ClickEvent(Event):
    """Mouse click event."""

    def __init__(self, col: int, row: int, cell_is_blank: bool) -> None:
        super().__init__()
        self.col: int = col
        """0-indexed screen column of the click."""
        self.row: int = row
        """0-indexed screen row of the click."""
        self.local_col: int = 0
        """Click column relative to the current handler's Box."""
        self.local_row: int = 0
        """Click row relative to the current handler's Box."""
        self.cell_is_blank: bool = cell_is_blank
        """True if the clicked cell has no visible content."""
