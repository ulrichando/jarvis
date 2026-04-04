"""Configuration constants. No imports - must remain dependency-free."""

from __future__ import annotations

from typing import Literal

NOTIFICATION_CHANNELS = (
    "auto",
    "iterm2",
    "iterm2_with_bell",
    "terminal_bell",
    "kitty",
    "ghostty",
    "notifications_disabled",
)

NotificationChannel = Literal[
    "auto",
    "iterm2",
    "iterm2_with_bell",
    "terminal_bell",
    "kitty",
    "ghostty",
    "notifications_disabled",
]

EDITOR_MODES = ("normal", "vim")
EditorMode = Literal["normal", "vim"]

TEAMMATE_MODES = ("auto", "tmux", "in-process")
TeammateMode = Literal["auto", "tmux", "in-process"]
