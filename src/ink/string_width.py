"""String width calculation for terminal display.

Determines the visual width of a string as it would appear in a terminal.
"""

from __future__ import annotations

import re
import unicodedata


def _is_zero_width(code_point: int) -> bool:
    """Check if a codepoint is zero-width."""
    # Fast path for common printable range
    if 0x20 <= code_point < 0x7F:
        return False
    if 0xA0 <= code_point < 0x0300:
        return code_point == 0x00AD

    # Control characters
    if code_point <= 0x1F or (0x7F <= code_point <= 0x9F):
        return True

    # Zero-width and invisible characters
    if (0x200B <= code_point <= 0x200D) or code_point == 0xFEFF or (0x2060 <= code_point <= 0x2064):
        return True

    # Variation selectors
    if (0xFE00 <= code_point <= 0xFE0F) or (0xE0100 <= code_point <= 0xE01EF):
        return True

    # Combining diacritical marks
    if (0x0300 <= code_point <= 0x036F) or (0x1AB0 <= code_point <= 0x1AFF) or \
       (0x1DC0 <= code_point <= 0x1DFF) or (0x20D0 <= code_point <= 0x20FF) or \
       (0xFE20 <= code_point <= 0xFE2F):
        return True

    # Surrogates, tag characters
    if 0xD800 <= code_point <= 0xDFFF:
        return True
    if 0xE0000 <= code_point <= 0xE007F:
        return True

    return False


def _is_wide(code_point: int) -> bool:
    """Check if a character is East Asian wide."""
    ea = unicodedata.east_asian_width(chr(code_point))
    return ea in ("W", "F")


# Strip ANSI escape sequences
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b\][^\x1b]*\x1b\\|\x1b[^[\]].?")


def string_width(s: str) -> int:
    """Get the display width of a string as it would appear in a terminal."""
    if not s:
        return 0

    # Fast path: pure ASCII
    is_pure_ascii = True
    for ch in s:
        code = ord(ch)
        if code >= 127 or code == 0x1B:
            is_pure_ascii = False
            break

    if is_pure_ascii:
        return sum(1 for ch in s if ord(ch) > 0x1F)

    # Strip ANSI if escape character is present
    if "\x1b" in s:
        s = _ANSI_RE.sub("", s)
        if not s:
            return 0

    width = 0
    for ch in s:
        cp = ord(ch)
        if _is_zero_width(cp):
            continue
        if _is_wide(cp):
            width += 2
        else:
            width += 1

    return width
