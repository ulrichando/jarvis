"""Terminal color application using ANSI escape codes."""

from __future__ import annotations

import re
from typing import Any, Literal

ColorType = Literal["foreground", "background"]

_RGB_REGEX = re.compile(r"^rgb\(\s?(\d+),\s?(\d+),\s?(\d+)\s?\)$")
_ANSI_REGEX = re.compile(r"^ansi256\(\s?(\d+)\s?\)$")

# ANSI color codes
_ANSI_FG = {
    "black": "\033[30m", "red": "\033[31m", "green": "\033[32m",
    "yellow": "\033[33m", "blue": "\033[34m", "magenta": "\033[35m",
    "cyan": "\033[36m", "white": "\033[37m",
    "blackBright": "\033[90m", "redBright": "\033[91m", "greenBright": "\033[92m",
    "yellowBright": "\033[93m", "blueBright": "\033[94m", "magentaBright": "\033[95m",
    "cyanBright": "\033[96m", "whiteBright": "\033[97m",
}

_ANSI_BG = {
    "black": "\033[40m", "red": "\033[41m", "green": "\033[42m",
    "yellow": "\033[43m", "blue": "\033[44m", "magenta": "\033[45m",
    "cyan": "\033[46m", "white": "\033[47m",
    "blackBright": "\033[100m", "redBright": "\033[101m", "greenBright": "\033[102m",
    "yellowBright": "\033[103m", "blueBright": "\033[104m", "magentaBright": "\033[105m",
    "cyanBright": "\033[106m", "whiteBright": "\033[107m",
}

_RESET = "\033[0m"


def colorize(s: str, color: str | None, type_: ColorType) -> str:
    """Apply a color to a string."""
    if not color:
        return s

    if color.startswith("ansi:"):
        value = color[len("ansi:"):]
        codes = _ANSI_FG if type_ == "foreground" else _ANSI_BG
        code = codes.get(value)
        if code:
            return f"{code}{s}{_RESET}"
        return s

    if color.startswith("#"):
        # Hex color
        hex_color = color.lstrip("#")
        if len(hex_color) == 6:
            r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
            if type_ == "foreground":
                return f"\033[38;2;{r};{g};{b}m{s}{_RESET}"
            else:
                return f"\033[48;2;{r};{g};{b}m{s}{_RESET}"
        return s

    if color.startswith("ansi256"):
        match = _ANSI_REGEX.match(color)
        if not match:
            return s
        value = int(match.group(1))
        if type_ == "foreground":
            return f"\033[38;5;{value}m{s}{_RESET}"
        else:
            return f"\033[48;5;{value}m{s}{_RESET}"

    if color.startswith("rgb"):
        match = _RGB_REGEX.match(color)
        if not match:
            return s
        r, g, b = int(match.group(1)), int(match.group(2)), int(match.group(3))
        if type_ == "foreground":
            return f"\033[38;2;{r};{g};{b}m{s}{_RESET}"
        else:
            return f"\033[48;2;{r};{g};{b}m{s}{_RESET}"

    return s


def apply_text_styles(text: str, styles: dict[str, Any]) -> str:
    """Apply TextStyles to a string."""
    result = text

    if styles.get("inverse"):
        result = f"\033[7m{result}\033[27m"
    if styles.get("strikethrough"):
        result = f"\033[9m{result}\033[29m"
    if styles.get("underline"):
        result = f"\033[4m{result}\033[24m"
    if styles.get("italic"):
        result = f"\033[3m{result}\033[23m"
    if styles.get("bold"):
        result = f"\033[1m{result}\033[22m"
    if styles.get("dim"):
        result = f"\033[2m{result}\033[22m"
    if styles.get("color"):
        result = colorize(result, styles["color"], "foreground")
    if styles.get("backgroundColor"):
        result = colorize(result, styles["backgroundColor"], "background")

    return result


def apply_color(text: str, color: str | None) -> str:
    """Apply a raw color value to text."""
    if not color:
        return text
    return colorize(text, color, "foreground")
