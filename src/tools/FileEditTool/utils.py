"""
Utility functions for the FileEditTool.
"""
from __future__ import annotations

import re
from typing import Optional

from src.tools.FileEditTool.types import EditInput, FileEdit


# Curly quote constants
LEFT_SINGLE_CURLY_QUOTE = "\u2018"
RIGHT_SINGLE_CURLY_QUOTE = "\u2019"
LEFT_DOUBLE_CURLY_QUOTE = "\u201c"
RIGHT_DOUBLE_CURLY_QUOTE = "\u201d"


def normalize_quotes(s: str) -> str:
    """Normalizes quotes by converting curly quotes to straight quotes."""
    return (
        s.replace(LEFT_SINGLE_CURLY_QUOTE, "'")
        .replace(RIGHT_SINGLE_CURLY_QUOTE, "'")
        .replace(LEFT_DOUBLE_CURLY_QUOTE, '"')
        .replace(RIGHT_DOUBLE_CURLY_QUOTE, '"')
    )


def strip_trailing_whitespace(s: str) -> str:
    """Strips trailing whitespace from each line while preserving line endings."""
    lines = re.split(r"(\r\n|\n|\r)", s)
    result = ""
    for i, part in enumerate(lines):
        if i % 2 == 0:
            result += part.rstrip()
        else:
            result += part
    return result


def find_actual_string(file_content: str, search_string: str) -> Optional[str]:
    """Find the actual string in file content that matches the search string.

    Uses a 5-layer matching strategy (same as Aider/production tools):
      1. Exact match
      2. Quote normalization
      3. Trailing whitespace normalization
      4. Relative indentation matching
      5. difflib block similarity (>0.85 threshold)
    """
    # Layer 1: exact
    if search_string in file_content:
        return search_string

    # Layer 2: quote normalization
    normalized_search = normalize_quotes(search_string)
    normalized_file = normalize_quotes(file_content)
    search_index = normalized_file.find(normalized_search)
    if search_index != -1:
        return file_content[search_index:search_index + len(search_string)]

    # Layer 3: trailing whitespace normalization
    ws_search = strip_trailing_whitespace(search_string)
    ws_file = strip_trailing_whitespace(file_content)
    search_index = ws_file.find(ws_search)
    if search_index != -1:
        # Map back to original file content position
        return file_content[search_index:search_index + len(search_string)]

    # Layer 4: relative indentation matching
    search_lines = search_string.splitlines()
    if search_lines:
        # Extract leading whitespace of first non-empty search line
        first_nonempty = next((l for l in search_lines if l.strip()), "")
        base_indent = len(first_nonempty) - len(first_nonempty.lstrip())
        # Strip that indent prefix from all search lines
        stripped_search_lines = []
        for line in search_lines:
            if line.startswith(" " * base_indent):
                stripped_search_lines.append(line[base_indent:])
            else:
                stripped_search_lines.append(line.lstrip())
        stripped_search = "\n".join(stripped_search_lines)

        file_lines = file_content.splitlines()
        for i in range(len(file_lines) - len(search_lines) + 1):
            window = file_lines[i:i + len(search_lines)]
            # Detect this window's indent level
            first_nonempty_w = next((l for l in window if l.strip()), "")
            window_indent = len(first_nonempty_w) - len(first_nonempty_w.lstrip())
            # Strip window's base indent
            stripped_window = "\n".join(
                l[window_indent:] if l.startswith(" " * window_indent) else l.lstrip()
                for l in window
            )
            if stripped_window == stripped_search:
                # Return the actual text from the file at this position
                start = sum(len(l) + 1 for l in file_lines[:i])
                length = sum(len(l) + 1 for l in file_lines[i:i + len(search_lines)])
                return file_content[start:start + length - 1]  # -1 to drop trailing \n

    # Layer 5: difflib block similarity (last resort, >0.85 threshold)
    import difflib as _dl
    search_lines = search_string.splitlines()
    file_lines = file_content.splitlines()
    if len(search_lines) >= 2 and len(file_lines) >= len(search_lines):
        best_ratio = 0.0
        best_start = -1
        window_size = len(search_lines)
        for i in range(len(file_lines) - window_size + 1):
            window = file_lines[i:i + window_size]
            ratio = _dl.SequenceMatcher(None, search_lines, window).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_start = i
        if best_ratio >= 0.85 and best_start >= 0:
            start = sum(len(l) + 1 for l in file_lines[:best_start])
            length = sum(len(l) + 1 for l in file_lines[best_start:best_start + window_size])
            return file_content[start:start + length - 1]

    return None


def _is_opening_context(chars: list[str], index: int) -> bool:
    if index == 0:
        return True
    prev = chars[index - 1]
    return prev in (" ", "\t", "\n", "\r", "(", "[", "{", "\u2014", "\u2013")


def _apply_curly_double_quotes(s: str) -> str:
    chars = list(s)
    result: list[str] = []
    for i, ch in enumerate(chars):
        if ch == '"':
            result.append(
                LEFT_DOUBLE_CURLY_QUOTE if _is_opening_context(chars, i)
                else RIGHT_DOUBLE_CURLY_QUOTE
            )
        else:
            result.append(ch)
    return "".join(result)


def _apply_curly_single_quotes(s: str) -> str:
    chars = list(s)
    result: list[str] = []
    for i, ch in enumerate(chars):
        if ch == "'":
            prev = chars[i - 1] if i > 0 else None
            nxt = chars[i + 1] if i < len(chars) - 1 else None
            prev_is_letter = prev is not None and prev.isalpha()
            next_is_letter = nxt is not None and nxt.isalpha()
            if prev_is_letter and next_is_letter:
                result.append(RIGHT_SINGLE_CURLY_QUOTE)
            else:
                result.append(
                    LEFT_SINGLE_CURLY_QUOTE if _is_opening_context(chars, i)
                    else RIGHT_SINGLE_CURLY_QUOTE
                )
        else:
            result.append(ch)
    return "".join(result)


def preserve_quote_style(
    old_string: str,
    actual_old_string: str,
    new_string: str,
) -> str:
    """When old_string matched via quote normalization, apply the same curly quote
    style to new_string so the edit preserves the file's typography.
    """
    if old_string == actual_old_string:
        return new_string

    has_double_quotes = (
        LEFT_DOUBLE_CURLY_QUOTE in actual_old_string
        or RIGHT_DOUBLE_CURLY_QUOTE in actual_old_string
    )
    has_single_quotes = (
        LEFT_SINGLE_CURLY_QUOTE in actual_old_string
        or RIGHT_SINGLE_CURLY_QUOTE in actual_old_string
    )

    if not has_double_quotes and not has_single_quotes:
        return new_string

    result = new_string
    if has_double_quotes:
        result = _apply_curly_double_quotes(result)
    if has_single_quotes:
        result = _apply_curly_single_quotes(result)

    return result


def apply_edit_to_file(
    original_content: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> str:
    """Apply a single edit to file content."""
    if replace_all:
        f = lambda content, search, replace: content.replace(search, replace)
    else:
        f = lambda content, search, replace: content.replace(search, replace, 1)

    if new_string != "":
        return f(original_content, old_string, new_string)

    strip_trailing_newline = (
        not old_string.endswith("\n")
        and old_string + "\n" in original_content
    )

    return (
        f(original_content, old_string + "\n", new_string)
        if strip_trailing_newline
        else f(original_content, old_string, new_string)
    )


def get_snippet(
    original_file: str,
    old_string: str,
    new_string: str,
    context_lines: int = 4,
) -> dict:
    """Gets a snippet from a file showing the context around a single edit."""
    before = original_file.split(old_string)[0] if old_string in original_file else original_file
    replacement_line = before.count("\n")
    new_file_lines = apply_edit_to_file(original_file, old_string, new_string).split("\n")

    start_line = max(0, replacement_line - context_lines)
    end_line = replacement_line + context_lines + new_string.count("\n") + 1

    snippet_lines = new_file_lines[start_line:end_line]
    snippet = "\n".join(snippet_lines)

    return {"snippet": snippet, "start_line": start_line + 1}


def are_file_edits_equivalent(
    edits1: list[FileEdit],
    edits2: list[FileEdit],
    original_content: str,
) -> bool:
    """Compare two sets of edits by applying both and comparing results."""
    if len(edits1) == len(edits2) and all(
        e1.old_string == e2.old_string
        and e1.new_string == e2.new_string
        and e1.replace_all == e2.replace_all
        for e1, e2 in zip(edits1, edits2)
    ):
        return True

    try:
        result1 = original_content
        for edit in edits1:
            result1 = apply_edit_to_file(
                result1, edit.old_string, edit.new_string, edit.replace_all
            )
    except Exception:
        result1 = None

    try:
        result2 = original_content
        for edit in edits2:
            result2 = apply_edit_to_file(
                result2, edit.old_string, edit.new_string, edit.replace_all
            )
    except Exception:
        result2 = None

    if result1 is None and result2 is None:
        return True
    if result1 is None or result2 is None:
        return False

    return result1 == result2
