"""
General string utility functions.
"""

from __future__ import annotations

import re
import unicodedata

MAX_STRING_LENGTH = 2**25


def escape_regexp(s: str) -> str:
    """Escape special regex characters in a string."""
    return re.escape(s)


def capitalize(s: str) -> str:
    """Uppercase the first character, leaving the rest unchanged."""
    if not s:
        return s
    return s[0].upper() + s[1:]


def plural(n: int, word: str, plural_word: str | None = None) -> str:
    """Return singular or plural form based on count."""
    if plural_word is None:
        plural_word = word + "s"
    return word if n == 1 else plural_word


def first_line_of(s: str) -> str:
    """Return the first line of a string."""
    nl = s.find("\n")
    return s if nl == -1 else s[:nl]


def count_char_in_string(s: str, char: str, start: int = 0) -> int:
    """Count occurrences of char in string."""
    return s.count(char, start)


def normalize_full_width_digits(s: str) -> str:
    """Normalize full-width (zenkaku) digits to half-width."""
    result = []
    for ch in s:
        code = ord(ch)
        if 0xFF10 <= code <= 0xFF19:  # Full-width 0-9
            result.append(chr(code - 0xFEE0))
        else:
            result.append(ch)
    return "".join(result)


def normalize_full_width_space(s: str) -> str:
    """Normalize full-width space to half-width."""
    return s.replace("\u3000", " ")


def safe_join_lines(
    lines: list[str],
    delimiter: str = ",",
    max_size: int = MAX_STRING_LENGTH,
) -> str:
    """Safely join strings with a delimiter, truncating if result exceeds max_size."""
    truncation_marker = "...[truncated]"
    result = ""

    for line in lines:
        delimiter_to_add = delimiter if result else ""
        full_addition = delimiter_to_add + line

        if len(result) + len(full_addition) <= max_size:
            result += full_addition
        else:
            remaining = (
                max_size - len(result) - len(delimiter_to_add) - len(truncation_marker)
            )
            if remaining > 0:
                result += delimiter_to_add + line[:remaining] + truncation_marker
            else:
                result += truncation_marker
            return result

    return result


class EndTruncatingAccumulator:
    """
    String accumulator that safely handles large outputs by truncating
    from the end when a size limit is exceeded.
    """

    def __init__(self, max_size: int = MAX_STRING_LENGTH) -> None:
        self._content = ""
        self._is_truncated = False
        self._total_bytes_received = 0
        self._max_size = max_size

    def append(self, data: str | bytes) -> None:
        """Append data, truncating if total exceeds max_size."""
        s = data if isinstance(data, str) else data.decode("utf-8", errors="replace")
        self._total_bytes_received += len(s)

        if self._is_truncated and len(self._content) >= self._max_size:
            return

        if len(self._content) + len(s) > self._max_size:
            remaining = self._max_size - len(self._content)
            if remaining > 0:
                self._content += s[:remaining]
            self._is_truncated = True
        else:
            self._content += s

    def __str__(self) -> str:
        if not self._is_truncated:
            return self._content
        truncated_bytes = self._total_bytes_received - self._max_size
        truncated_kb = round(truncated_bytes / 1024)
        return self._content + f"\n... [output truncated - {truncated_kb}KB removed]"

    def clear(self) -> None:
        self._content = ""
        self._is_truncated = False
        self._total_bytes_received = 0

    @property
    def length(self) -> int:
        return len(self._content)

    @property
    def truncated(self) -> bool:
        return self._is_truncated

    @property
    def total_bytes(self) -> int:
        return self._total_bytes_received


def truncate_to_lines(text: str, max_lines: int) -> str:
    """Truncate text to a maximum number of lines."""
    lines = text.split("\n")
    if len(lines) <= max_lines:
        return text
    return "\n".join(lines[:max_lines]) + "..."
