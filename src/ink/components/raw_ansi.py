"""RawAnsi component - renders pre-formatted ANSI strings."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RawAnsiProps:
    content: str = ""
    width: int = 0
    height: int = 0


class RawAnsi:
    """Renders pre-formatted ANSI strings with known dimensions.

    No stringWidth, no wrapping, no tab expansion -- the producer
    already wrapped to the target width.
    """

    def __init__(self, props: RawAnsiProps | None = None) -> None:
        self.props = props or RawAnsiProps()
