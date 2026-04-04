"""useTerminalNotification hook - send desktop notifications via terminal."""

from __future__ import annotations

import os
import sys
from typing import Any

from .termio.osc import osc, wrapForMultiplexer, OSC


def send_terminal_notification(
    title: str,
    body: str = "",
    subtitle: str = "",
) -> None:
    """Send a desktop notification via terminal escape sequences.

    Supports iTerm2 (OSC 9) and Kitty (OSC 99) notification protocols.
    Falls back to Ghostty (OSC 777) on supported terminals.
    """
    term_program = os.environ.get("TERM_PROGRAM", "")

    if term_program in ("iTerm.app", "iTerm2"):
        # iTerm2 notification: OSC 9 ; 0 ; body BEL
        seq = osc(OSC.ITERM2, 0, body or title)
    elif term_program == "kitty" or "kitty" in os.environ.get("TERM", ""):
        # Kitty notification: OSC 99 ; ... BEL
        seq = osc(OSC.KITTY, f"d=0:p=title;{title}")
        if body:
            seq += osc(OSC.KITTY, f"d=1:p=body;{body}")
    else:
        # Ghostty notification
        seq = osc(OSC.GHOSTTY, f"notify;{title};{body}")

    seq = wrapForMultiplexer(seq)
    sys.stdout.write(seq)
    sys.stdout.flush()
