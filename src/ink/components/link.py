"""Link component - renders a hyperlink."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class LinkProps:
    href: str = ""
    fallback: bool = True


class Link:
    """Renders an OSC 8 hyperlink. In TS this renders an ink-link DOM element."""

    def __init__(self, props: LinkProps | None = None) -> None:
        self.props = props or LinkProps()
