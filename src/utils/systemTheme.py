"""
Terminal dark/light mode detection for the 'auto' theme setting.

Detection is based on the terminal's actual background color (queried via
OSC 11) rather than the OS appearance setting -- a dark terminal on a
light-mode OS should still resolve to 'dark'.
"""

from __future__ import annotations

import os
import re
from typing import Literal, Optional

SystemTheme = Literal["dark", "light"]
ThemeName = Literal["dark", "light"]
ThemeSetting = Literal["dark", "light", "auto"]

_cached_system_theme: Optional[SystemTheme] = None


def get_system_theme_name() -> SystemTheme:
    """
    Get the current terminal theme.
    Cached after first detection; the watcher updates the cache on live changes.
    """
    global _cached_system_theme
    if _cached_system_theme is None:
        _cached_system_theme = _detect_from_colorfgbg() or "dark"
    return _cached_system_theme


def set_cached_system_theme(theme: SystemTheme) -> None:
    """
    Update the cached terminal theme.
    Called by the watcher when the OSC 11 query returns.
    """
    global _cached_system_theme
    _cached_system_theme = theme


def resolve_theme_setting(setting: ThemeSetting) -> ThemeName:
    """Resolve a ThemeSetting (which may be 'auto') to a concrete ThemeName."""
    if setting == "auto":
        return get_system_theme_name()
    return setting


def theme_from_osc_color(data: str) -> Optional[SystemTheme]:
    """
    Parse an OSC color response data string into a theme.

    Accepts XParseColor formats returned by OSC 10/11 queries:
    - ``rgb:R/G/B`` where each component is 1-4 hex digits.
    - ``#RRGGBB`` / ``#RRRRGGGGBBBB`` (rare, but cheap to accept).

    Returns None for unrecognized formats.
    """
    rgb = _parse_osc_rgb(data)
    if rgb is None:
        return None
    # ITU-R BT.709 relative luminance
    luminance = 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]
    return "light" if luminance > 0.5 else "dark"


def _parse_osc_rgb(data: str) -> Optional[tuple[float, float, float]]:
    """Parse an OSC rgb response into normalized (r, g, b) floats."""
    # rgb:RRRR/GGGG/BBBB
    m = re.match(
        r"^rgba?:([0-9a-fA-F]{1,4})/([0-9a-fA-F]{1,4})/([0-9a-fA-F]{1,4})",
        data,
    )
    if m:
        return (
            _hex_component(m.group(1)),
            _hex_component(m.group(2)),
            _hex_component(m.group(3)),
        )
    # #RRGGBB or #RRRRGGGGBBBB
    m = re.match(r"^#([0-9a-fA-F]+)$", data)
    if m and len(m.group(1)) % 3 == 0:
        h = m.group(1)
        n = len(h) // 3
        return (
            _hex_component(h[:n]),
            _hex_component(h[n : 2 * n]),
            _hex_component(h[2 * n :]),
        )
    return None


def _hex_component(hex_str: str) -> float:
    """Normalize a 1-4 digit hex component to [0, 1]."""
    max_val = 16 ** len(hex_str) - 1
    return int(hex_str, 16) / max_val


def _detect_from_colorfgbg() -> Optional[SystemTheme]:
    """
    Read $COLORFGBG for a synchronous initial guess.
    Format is ``fg;bg`` (or ``fg;other;bg``) where values are ANSI color indices.
    rxvt convention: bg 0-6 or 8 are dark; bg 7 and 9-15 are light.
    """
    colorfgbg = os.environ.get("COLORFGBG")
    if not colorfgbg:
        return None
    parts = colorfgbg.split(";")
    bg = parts[-1] if parts else None
    if bg is None or bg == "":
        return None
    try:
        bg_num = int(bg)
    except ValueError:
        return None
    if bg_num < 0 or bg_num > 15:
        return None
    return "dark" if bg_num <= 6 or bg_num == 8 else "light"
