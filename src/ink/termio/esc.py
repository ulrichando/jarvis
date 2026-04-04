"""ESC Sequence Parser.

Handles simple escape sequences: ESC + one or two characters.
"""

from __future__ import annotations

from typing import Any


def parse_esc(chars: str) -> dict[str, Any] | None:
    """Parse a simple ESC sequence.

    Args:
        chars: Characters after ESC (not including ESC itself).

    Returns:
        Action dict or None.
    """
    if not chars:
        return None

    first = chars[0]

    # Full reset (RIS)
    if first == "c":
        return {"type": "reset"}

    # Cursor save (DECSC)
    if first == "7":
        return {"type": "cursor", "action": {"type": "save"}}

    # Cursor restore (DECRC)
    if first == "8":
        return {"type": "cursor", "action": {"type": "restore"}}

    # Index - move cursor down (IND)
    if first == "D":
        return {
            "type": "cursor",
            "action": {"type": "move", "direction": "down", "count": 1},
        }

    # Reverse index - move cursor up (RI)
    if first == "M":
        return {
            "type": "cursor",
            "action": {"type": "move", "direction": "up", "count": 1},
        }

    # Next line (NEL)
    if first == "E":
        return {"type": "cursor", "action": {"type": "nextLine", "count": 1}}

    # Horizontal tab set (HTS)
    if first == "H":
        return None

    # Charset selection - silently ignore
    if first in "()" and len(chars) >= 2:
        return None

    # Unknown
    return {"type": "unknown", "sequence": f"\x1b{chars}"}
