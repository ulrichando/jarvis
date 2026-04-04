"""
Unicode Sanitization for Hidden Character Attack Mitigation

Implements security measures against Unicode-based hidden character attacks,
specifically targeting ASCII Smuggling and Hidden Prompt Injection vulnerabilities.
These attacks use invisible Unicode characters (such as Tag characters, format controls,
private use areas, and noncharacters) to hide malicious instructions that are invisible
to users but processed by AI models.

Reference: https://embracethered.com/blog/posts/2024/hiding-and-finding-text-with-unicode-tags/
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, TypeVar, Union, overload

T = TypeVar("T")

MAX_ITERATIONS = 10


def partially_sanitize_unicode(prompt: str) -> str:
    """
    Sanitize a string by removing dangerous Unicode characters.

    Applies NFKC normalization and strips format characters (Cf),
    private use (Co), unassigned (Cn) categories, plus explicit
    dangerous ranges as a fallback.
    """
    current = prompt
    previous = ""
    iterations = 0

    while current != previous and iterations < MAX_ITERATIONS:
        previous = current

        # Apply NFKC normalization
        current = unicodedata.normalize("NFKC", current)

        # Remove dangerous Unicode categories (Cf, Co, Cn)
        current = "".join(
            ch
            for ch in current
            if unicodedata.category(ch) not in ("Cf", "Co", "Cn")
        )

        # Explicit fallback ranges
        # Zero-width spaces, LTR/RTL marks
        current = re.sub(r"[\u200B-\u200F]", "", current)
        # Directional formatting characters
        current = re.sub(r"[\u202A-\u202E]", "", current)
        # Directional isolates
        current = re.sub(r"[\u2066-\u2069]", "", current)
        # Byte order mark
        current = current.replace("\uFEFF", "")
        # Basic Multilingual Plane private use
        current = re.sub(r"[\uE000-\uF8FF]", "", current)

        iterations += 1

    if iterations >= MAX_ITERATIONS:
        raise RuntimeError(
            f"Unicode sanitization reached maximum iterations ({MAX_ITERATIONS}) "
            f"for input: {prompt[:100]}"
        )

    return current


@overload
def recursively_sanitize_unicode(value: str) -> str: ...


@overload
def recursively_sanitize_unicode(value: list) -> list: ...


@overload
def recursively_sanitize_unicode(value: dict) -> dict: ...


@overload
def recursively_sanitize_unicode(value: T) -> T: ...


def recursively_sanitize_unicode(value: Any) -> Any:
    """
    Recursively sanitize Unicode in strings, lists, and dicts.

    Non-string primitives (int, float, bool, None) are returned unchanged.
    """
    if isinstance(value, str):
        return partially_sanitize_unicode(value)

    if isinstance(value, list):
        return [recursively_sanitize_unicode(item) for item in value]

    if isinstance(value, dict):
        return {
            recursively_sanitize_unicode(k): recursively_sanitize_unicode(v)
            for k, v in value.items()
        }

    return value
