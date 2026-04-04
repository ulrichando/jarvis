"""DEC (Digital Equipment Corporation) Private Mode Sequences.

DEC private modes use CSI ? N h (set) and CSI ? N l (reset) format.
"""

from .csi import csi


class DEC:
    """DEC private mode numbers."""
    CURSOR_VISIBLE = 25
    ALT_SCREEN = 47
    ALT_SCREEN_CLEAR = 1049
    MOUSE_NORMAL = 1000
    MOUSE_BUTTON = 1002
    MOUSE_ANY = 1003
    MOUSE_SGR = 1006
    FOCUS_EVENTS = 1004
    BRACKETED_PASTE = 2004
    SYNCHRONIZED_UPDATE = 2026


def decset(mode: int) -> str:
    """Generate CSI ? N h sequence (set mode)."""
    return csi(f"?{mode}h")


def decreset(mode: int) -> str:
    """Generate CSI ? N l sequence (reset mode)."""
    return csi(f"?{mode}l")


# Pre-generated sequences for common modes
BSU = decset(DEC.SYNCHRONIZED_UPDATE)
ESU = decreset(DEC.SYNCHRONIZED_UPDATE)
EBP = decset(DEC.BRACKETED_PASTE)
DBP = decreset(DEC.BRACKETED_PASTE)
EFE = decset(DEC.FOCUS_EVENTS)
DFE = decreset(DEC.FOCUS_EVENTS)
SHOW_CURSOR = decset(DEC.CURSOR_VISIBLE)
HIDE_CURSOR = decreset(DEC.CURSOR_VISIBLE)
ENTER_ALT_SCREEN = decset(DEC.ALT_SCREEN_CLEAR)
EXIT_ALT_SCREEN = decreset(DEC.ALT_SCREEN_CLEAR)

ENABLE_MOUSE_TRACKING = (
    decset(DEC.MOUSE_NORMAL)
    + decset(DEC.MOUSE_BUTTON)
    + decset(DEC.MOUSE_ANY)
    + decset(DEC.MOUSE_SGR)
)
DISABLE_MOUSE_TRACKING = (
    decreset(DEC.MOUSE_SGR)
    + decreset(DEC.MOUSE_ANY)
    + decreset(DEC.MOUSE_BUTTON)
    + decreset(DEC.MOUSE_NORMAL)
)
