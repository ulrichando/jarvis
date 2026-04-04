"""Portable Chrome extension setup utilities."""

from __future__ import annotations

from typing import Literal

ChromiumBrowser = Literal["chrome", "edge", "brave", "opera", "vivaldi", "arc"]

SUPPORTED_BROWSERS: list[ChromiumBrowser] = [
    "chrome", "edge", "brave", "opera", "vivaldi", "arc"
]


def get_supported_browsers() -> list[ChromiumBrowser]:
    """Get list of supported Chromium browsers."""
    return SUPPORTED_BROWSERS
