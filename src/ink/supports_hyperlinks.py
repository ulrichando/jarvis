"""Detect whether the terminal supports OSC 8 hyperlinks."""

from __future__ import annotations

import os

ADDITIONAL_HYPERLINK_TERMINALS = [
    "ghostty", "Hyper", "kitty", "alacritty", "iTerm.app", "iTerm2",
]


def supports_hyperlinks(env: dict[str, str] | None = None) -> bool:
    """Returns whether stdout supports OSC 8 hyperlinks."""
    if env is None:
        env = dict(os.environ)

    term_program = env.get("TERM_PROGRAM")
    if term_program and term_program in ADDITIONAL_HYPERLINK_TERMINALS:
        return True

    lc_terminal = env.get("LC_TERMINAL")
    if lc_terminal and lc_terminal in ADDITIONAL_HYPERLINK_TERMINALS:
        return True

    term = env.get("TERM")
    if term and "kitty" in term:
        return True

    return False
