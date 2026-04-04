"""NoSelect component - excludes content from text selection."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class NoSelectProps:
    from_left_edge: bool = False


class NoSelect:
    """Excludes this box's cells from text selection in fullscreen mode."""

    def __init__(self, props: NoSelectProps | None = None) -> None:
        self.props = props or NoSelectProps()
