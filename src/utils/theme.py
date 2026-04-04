"""
Theme system with color palettes for different display modes.

Supports dark, light, daltonized, and ANSI-only themes.
"""

from dataclasses import dataclass
from typing import Dict, Literal, Tuple

ThemeName = Literal[
    "dark", "light", "light-daltonized", "dark-daltonized",
    "light-ansi", "dark-ansi",
]

THEME_NAMES: Tuple[ThemeName, ...] = (
    "dark", "light", "light-daltonized", "dark-daltonized",
    "light-ansi", "dark-ansi",
)

ThemeSetting = Literal[
    "auto", "dark", "light", "light-daltonized", "dark-daltonized",
    "light-ansi", "dark-ansi",
]

THEME_SETTINGS: Tuple[str, ...] = ("auto",) + THEME_NAMES


# Theme is a dict mapping color key names to color strings
# Color strings can be "rgb(r,g,b)" or "ansi:colorName"
Theme = Dict[str, str]


def _make_light_theme() -> Theme:
    return {
        "autoAccept": "rgb(135,0,255)",
        "bashBorder": "rgb(255,0,135)",
        "claude": "rgb(215,119,87)",
        "claudeShimmer": "rgb(245,149,117)",
        "permission": "rgb(87,105,247)",
        "permissionShimmer": "rgb(137,155,255)",
        "planMode": "rgb(0,102,102)",
        "text": "rgb(0,0,0)",
        "inverseText": "rgb(255,255,255)",
        "inactive": "rgb(102,102,102)",
        "subtle": "rgb(175,175,175)",
        "suggestion": "rgb(87,105,247)",
        "success": "rgb(44,122,57)",
        "error": "rgb(171,43,63)",
        "warning": "rgb(150,108,30)",
        "diffAdded": "rgb(105,219,124)",
        "diffRemoved": "rgb(255,168,180)",
        "diffAddedWord": "rgb(47,157,68)",
        "diffRemovedWord": "rgb(209,69,75)",
    }


def _make_dark_theme() -> Theme:
    return {
        "autoAccept": "rgb(175,135,255)",
        "bashBorder": "rgb(253,93,177)",
        "claude": "rgb(215,119,87)",
        "claudeShimmer": "rgb(235,159,127)",
        "permission": "rgb(177,185,249)",
        "permissionShimmer": "rgb(207,215,255)",
        "planMode": "rgb(72,150,140)",
        "text": "rgb(255,255,255)",
        "inverseText": "rgb(0,0,0)",
        "inactive": "rgb(153,153,153)",
        "subtle": "rgb(80,80,80)",
        "suggestion": "rgb(177,185,249)",
        "success": "rgb(78,186,101)",
        "error": "rgb(255,107,128)",
        "warning": "rgb(255,193,7)",
        "diffAdded": "rgb(34,92,43)",
        "diffRemoved": "rgb(122,41,54)",
        "diffAddedWord": "rgb(56,166,96)",
        "diffRemovedWord": "rgb(179,89,107)",
    }


def _make_dark_ansi_theme() -> Theme:
    return {
        "autoAccept": "ansi:magentaBright",
        "bashBorder": "ansi:magentaBright",
        "claude": "ansi:redBright",
        "permission": "ansi:blueBright",
        "planMode": "ansi:cyanBright",
        "text": "ansi:whiteBright",
        "inverseText": "ansi:black",
        "inactive": "ansi:white",
        "subtle": "ansi:white",
        "suggestion": "ansi:blueBright",
        "success": "ansi:greenBright",
        "error": "ansi:redBright",
        "warning": "ansi:yellowBright",
        "diffAdded": "ansi:green",
        "diffRemoved": "ansi:red",
        "diffAddedWord": "ansi:greenBright",
        "diffRemovedWord": "ansi:redBright",
    }


def _make_light_ansi_theme() -> Theme:
    return {
        "autoAccept": "ansi:magenta",
        "bashBorder": "ansi:magenta",
        "claude": "ansi:redBright",
        "permission": "ansi:blue",
        "planMode": "ansi:cyan",
        "text": "ansi:black",
        "inverseText": "ansi:white",
        "inactive": "ansi:blackBright",
        "subtle": "ansi:blackBright",
        "suggestion": "ansi:blue",
        "success": "ansi:green",
        "error": "ansi:red",
        "warning": "ansi:yellow",
        "diffAdded": "ansi:green",
        "diffRemoved": "ansi:red",
        "diffAddedWord": "ansi:greenBright",
        "diffRemovedWord": "ansi:redBright",
    }


_THEMES: Dict[ThemeName, Theme] = {
    "light": _make_light_theme(),
    "dark": _make_dark_theme(),
    "light-ansi": _make_light_ansi_theme(),
    "dark-ansi": _make_dark_ansi_theme(),
    "light-daltonized": _make_light_theme(),  # Uses light as base
    "dark-daltonized": _make_dark_theme(),    # Uses dark as base
}


def get_theme(theme_name: ThemeName) -> Theme:
    """Get a theme by name."""
    return _THEMES.get(theme_name, _THEMES["dark"])


def theme_color_to_ansi(theme_color: str) -> str:
    """
    Converts a theme color to an ANSI escape sequence.
    Parses rgb(r,g,b) format or returns fallback.
    """
    import re
    rgb_match = re.match(r"rgb\(\s?(\d+),\s?(\d+),\s?(\d+)\s?\)", theme_color)
    if rgb_match:
        r = int(rgb_match.group(1))
        g = int(rgb_match.group(2))
        b = int(rgb_match.group(3))
        return f"\033[38;2;{r};{g};{b}m"
    # Fallback to magenta
    return "\033[35m"
