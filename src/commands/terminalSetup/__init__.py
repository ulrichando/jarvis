"""Terminal-setup command - Install key bindings for newlines."""

from __future__ import annotations

import os

# Terminals that natively support CSI u / Kitty keyboard protocol
NATIVE_CSIU_TERMINALS = {
    "ghostty": "Ghostty",
    "kitty": "Kitty",
    "iTerm.app": "iTerm2",
    "WezTerm": "WezTerm",
}

terminal = os.environ.get("TERM_PROGRAM", "")

command = {
    "type": "local",
    "name": "terminal-setup",
    "description": (
        "Enable Option+Enter key binding for newlines and visual bell"
        if terminal == "Apple_Terminal"
        else "Install Shift+Enter key binding for newlines"
    ),
    "is_hidden": terminal in NATIVE_CSIU_TERMINALS,
}
