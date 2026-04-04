"""CSI (Control Sequence Introducer) Types.

Enums and types for CSI command parameters.
"""

from .ansi import ESC, ESC_TYPE, SEP

CSI_PREFIX = ESC + chr(ESC_TYPE.CSI)


class CSI_RANGE:
    """CSI parameter byte ranges."""
    PARAM_START = 0x30
    PARAM_END = 0x3F
    INTERMEDIATE_START = 0x20
    INTERMEDIATE_END = 0x2F
    FINAL_START = 0x40
    FINAL_END = 0x7E


def is_csi_param(byte: int) -> bool:
    """Check if a byte is a CSI parameter byte."""
    return CSI_RANGE.PARAM_START <= byte <= CSI_RANGE.PARAM_END


def is_csi_intermediate(byte: int) -> bool:
    """Check if a byte is a CSI intermediate byte."""
    return CSI_RANGE.INTERMEDIATE_START <= byte <= CSI_RANGE.INTERMEDIATE_END


def is_csi_final(byte: int) -> bool:
    """Check if a byte is a CSI final byte (@ through ~)."""
    return CSI_RANGE.FINAL_START <= byte <= CSI_RANGE.FINAL_END


def csi(*args: str | int) -> str:
    """Generate a CSI sequence: ESC [ p1;p2;...;pN final."""
    if len(args) == 0:
        return CSI_PREFIX
    if len(args) == 1:
        return f"{CSI_PREFIX}{args[0]}"
    params = args[:-1]
    final = args[-1]
    return f"{CSI_PREFIX}{SEP.join(str(p) for p in params)}{final}"


class CSI:
    """CSI final bytes - the command identifier."""
    # Cursor movement
    CUU = 0x41  # A - Cursor Up
    CUD = 0x42  # B - Cursor Down
    CUF = 0x43  # C - Cursor Forward
    CUB = 0x44  # D - Cursor Back
    CNL = 0x45  # E - Cursor Next Line
    CPL = 0x46  # F - Cursor Previous Line
    CHA = 0x47  # G - Cursor Horizontal Absolute
    CUP = 0x48  # H - Cursor Position
    CHT = 0x49  # I - Cursor Horizontal Tab
    VPA = 0x64  # d - Vertical Position Absolute
    HVP = 0x66  # f - Horizontal Vertical Position

    # Erase
    ED = 0x4A   # J - Erase in Display
    EL = 0x4B   # K - Erase in Line
    ECH = 0x58  # X - Erase Character

    # Insert/Delete
    IL = 0x4C   # L - Insert Lines
    DL = 0x4D   # M - Delete Lines
    ICH = 0x40  # @ - Insert Characters
    DCH = 0x50  # P - Delete Characters

    # Scroll
    SU = 0x53   # S - Scroll Up
    SD = 0x54   # T - Scroll Down

    # Modes
    SM = 0x68   # h - Set Mode
    RM = 0x6C   # l - Reset Mode

    # SGR
    SGR = 0x6D  # m - Select Graphic Rendition

    # Other
    DSR = 0x6E       # n - Device Status Report
    DECSCUSR = 0x71  # q - Set Cursor Style (with space intermediate)
    DECSTBM = 0x72   # r - Set Top and Bottom Margins
    SCOSC = 0x73     # s - Save Cursor Position
    SCORC = 0x75     # u - Restore Cursor Position
    CBT = 0x5A       # Z - Cursor Backward Tabulation


# Erase in Display regions
ERASE_DISPLAY = ("toEnd", "toStart", "all", "scrollback")

# Erase in Line regions
ERASE_LINE_REGION = ("toEnd", "toStart", "all")

# Cursor styles (DECSCUSR)
CursorStyle = str  # 'block' | 'underline' | 'bar'

CURSOR_STYLES = [
    {"style": "block", "blinking": True},      # 0 - default
    {"style": "block", "blinking": True},      # 1
    {"style": "block", "blinking": False},     # 2
    {"style": "underline", "blinking": True},  # 3
    {"style": "underline", "blinking": False}, # 4
    {"style": "bar", "blinking": True},        # 5
    {"style": "bar", "blinking": False},       # 6
]


# Cursor movement generators

def cursor_up(n: int = 1) -> str:
    return "" if n == 0 else csi(n, "A")


def cursor_down(n: int = 1) -> str:
    return "" if n == 0 else csi(n, "B")


def cursor_forward(n: int = 1) -> str:
    return "" if n == 0 else csi(n, "C")


def cursor_back(n: int = 1) -> str:
    return "" if n == 0 else csi(n, "D")


def cursor_to(col: int) -> str:
    return csi(col, "G")


CURSOR_LEFT = csi("G")


def cursor_position(row: int, col: int) -> str:
    return csi(row, col, "H")


CURSOR_HOME = csi("H")


def cursor_move(x: int, y: int) -> str:
    """Move cursor relative to current position."""
    result = ""
    if x < 0:
        result += cursor_back(-x)
    elif x > 0:
        result += cursor_forward(x)
    if y < 0:
        result += cursor_up(-y)
    elif y > 0:
        result += cursor_down(y)
    return result


# Save/restore cursor position
CURSOR_SAVE = csi("s")
CURSOR_RESTORE = csi("u")


# Erase generators

def erase_to_end_of_line() -> str:
    return csi("K")


def erase_to_start_of_line() -> str:
    return csi(1, "K")


def erase_line_fn() -> str:
    return csi(2, "K")


ERASE_LINE = csi(2, "K")


def erase_to_end_of_screen() -> str:
    return csi("J")


def erase_to_start_of_screen() -> str:
    return csi(1, "J")


def erase_screen_fn() -> str:
    return csi(2, "J")


ERASE_SCREEN = csi(2, "J")
ERASE_SCROLLBACK = csi(3, "J")


def erase_lines(n: int) -> str:
    """Erase n lines starting from cursor line, moving cursor up."""
    if n <= 0:
        return ""
    result = ""
    for i in range(n):
        result += ERASE_LINE
        if i < n - 1:
            result += cursor_up(1)
    result += CURSOR_LEFT
    return result


# Scroll
def scroll_up(n: int = 1) -> str:
    return "" if n == 0 else csi(n, "S")


def scroll_down(n: int = 1) -> str:
    return "" if n == 0 else csi(n, "T")


def set_scroll_region(top: int, bottom: int) -> str:
    """Set scroll region (DECSTBM). 1-indexed, inclusive."""
    return csi(top, bottom, "r")


RESET_SCROLL_REGION = csi("r")

# Bracketed paste markers
PASTE_START = csi("200~")
PASTE_END = csi("201~")

# Focus event markers
FOCUS_IN = csi("I")
FOCUS_OUT = csi("O")

# Kitty keyboard protocol
ENABLE_KITTY_KEYBOARD = csi(">1u")
DISABLE_KITTY_KEYBOARD = csi("<u")
ENABLE_MODIFY_OTHER_KEYS = csi(">4;2m")
DISABLE_MODIFY_OTHER_KEYS = csi(">4m")
