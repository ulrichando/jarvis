"""
Parser for sed edit commands (-i flag substitutions).
Extracts file paths and substitution patterns to enable file-edit-style rendering.
"""
from __future__ import annotations

import os
import re
import secrets
from dataclasses import dataclass
from typing import Optional


@dataclass
class SedEditInfo:
    """Information about a sed in-place edit command."""
    file_path: str
    pattern: str
    replacement: str
    flags: str
    extended_regex: bool


def is_sed_in_place_edit(command: str) -> bool:
    """Check if a command is a sed in-place edit command."""
    return parse_sed_edit_command(command) is not None


def parse_sed_edit_command(command: str) -> Optional[SedEditInfo]:
    """Parse a sed edit command and extract the edit information.
    Returns None if the command is not a valid sed in-place edit.
    """
    trimmed = command.strip()

    # Must start with sed
    sed_match = re.match(r"^\s*sed\s+", trimmed)
    if not sed_match:
        return None

    without_sed = trimmed[sed_match.end():]

    # Simple tokenization (not full shell parsing, but adequate for common cases)
    try:
        tokens = _simple_tokenize(without_sed)
    except ValueError:
        return None

    # Parse flags and arguments
    has_in_place_flag = False
    extended_regex = False
    expression: Optional[str] = None
    file_path: Optional[str] = None

    i = 0
    while i < len(tokens):
        arg = tokens[i]

        # Handle -i flag
        if arg in ("-i", "--in-place"):
            has_in_place_flag = True
            i += 1
            # On macOS, -i requires a suffix argument (even if empty string)
            if i < len(tokens):
                next_arg = tokens[i]
                if not next_arg.startswith("-") and (next_arg == "" or next_arg.startswith(".")):
                    i += 1  # Skip backup suffix
            continue
        if arg.startswith("-i"):
            has_in_place_flag = True
            i += 1
            continue

        # Handle extended regex flags
        if arg in ("-E", "-r", "--regexp-extended"):
            extended_regex = True
            i += 1
            continue

        # Handle -e flag with expression
        if arg in ("-e", "--expression"):
            if i + 1 < len(tokens):
                if expression is not None:
                    return None
                expression = tokens[i + 1]
                i += 2
                continue
            return None
        if arg.startswith("--expression="):
            if expression is not None:
                return None
            expression = arg[len("--expression="):]
            i += 1
            continue

        # Skip other flags
        if arg.startswith("-"):
            return None

        # Non-flag argument
        if expression is None:
            expression = arg
        elif file_path is None:
            file_path = arg
        else:
            return None  # More than one file

        i += 1

    if not has_in_place_flag or not expression or not file_path:
        return None

    # Parse the substitution expression: s/pattern/replacement/flags
    if not expression.startswith("s/"):
        return None

    rest = expression[2:]  # Skip 's/'

    # Find pattern and replacement by tracking escaped characters
    pattern_str = ""
    replacement_str = ""
    flags_str = ""
    state = "pattern"  # pattern -> replacement -> flags
    j = 0

    while j < len(rest):
        char = rest[j]

        if char == "\\" and j + 1 < len(rest):
            target = {"pattern": "p", "replacement": "r", "flags": "f"}
            if state == "pattern":
                pattern_str += char + rest[j + 1]
            elif state == "replacement":
                replacement_str += char + rest[j + 1]
            else:
                flags_str += char + rest[j + 1]
            j += 2
            continue

        if char == "/":
            if state == "pattern":
                state = "replacement"
            elif state == "replacement":
                state = "flags"
            else:
                return None
            j += 1
            continue

        if state == "pattern":
            pattern_str += char
        elif state == "replacement":
            replacement_str += char
        else:
            flags_str += char
        j += 1

    if state != "flags":
        return None

    # Validate flags
    if not re.match(r"^[gpimIM1-9]*$", flags_str):
        return None

    return SedEditInfo(
        file_path=file_path,
        pattern=pattern_str,
        replacement=replacement_str,
        flags=flags_str,
        extended_regex=extended_regex,
    )


def apply_sed_substitution(content: str, sed_info: SedEditInfo) -> str:
    """Apply a sed substitution to file content.
    Returns the new content after applying the substitution.
    """
    # Build regex flags
    re_flags = 0
    if "i" in sed_info.flags or "I" in sed_info.flags:
        re_flags |= re.IGNORECASE
    if "m" in sed_info.flags or "M" in sed_info.flags:
        re_flags |= re.MULTILINE

    # Convert sed pattern to Python regex
    py_pattern = sed_info.pattern.replace("\\/", "/")

    # BRE -> ERE conversion (if not in extended mode)
    if not sed_info.extended_regex:
        # Placeholder-based conversion
        ph = secrets.token_hex(4)
        bp = f"\x00BS{ph}\x00"
        pp = f"\x00PL{ph}\x00"
        qp = f"\x00QU{ph}\x00"
        pip = f"\x00PI{ph}\x00"
        lp = f"\x00LP{ph}\x00"
        rp = f"\x00RP{ph}\x00"

        py_pattern = py_pattern.replace("\\\\", bp)
        py_pattern = py_pattern.replace("\\+", pp)
        py_pattern = py_pattern.replace("\\?", qp)
        py_pattern = py_pattern.replace("\\|", pip)
        py_pattern = py_pattern.replace("\\(", lp)
        py_pattern = py_pattern.replace("\\)", rp)
        py_pattern = py_pattern.replace("+", "\\+")
        py_pattern = py_pattern.replace("?", "\\?")
        py_pattern = py_pattern.replace("|", "\\|")
        py_pattern = py_pattern.replace("(", "\\(")
        py_pattern = py_pattern.replace(")", "\\)")
        py_pattern = py_pattern.replace(bp, "\\\\")
        py_pattern = py_pattern.replace(pp, "+")
        py_pattern = py_pattern.replace(qp, "?")
        py_pattern = py_pattern.replace(pip, "|")
        py_pattern = py_pattern.replace(lp, "(")
        py_pattern = py_pattern.replace(rp, ")")

    # Convert replacement
    py_replacement = sed_info.replacement.replace("\\/", "/")

    # Handle & (full match reference) -> \g<0>
    salt = secrets.token_hex(8)
    escaped_amp_ph = f"___ESCAPED_AMPERSAND_{salt}___"
    py_replacement = py_replacement.replace("\\&", escaped_amp_ph)
    py_replacement = py_replacement.replace("&", r"\g<0>")
    py_replacement = py_replacement.replace(escaped_amp_ph, "&")

    try:
        regex = re.compile(py_pattern, re_flags)
        count = 0 if "g" in sed_info.flags else 1
        return regex.sub(py_replacement, content, count=count)
    except re.error:
        return content


def _simple_tokenize(s: str) -> list[str]:
    """Simple shell-like tokenization. Handles single/double quotes."""
    tokens: list[str] = []
    current = ""
    i = 0
    while i < len(s):
        c = s[i]
        if c in (" ", "\t"):
            if current:
                tokens.append(current)
                current = ""
            i += 1
        elif c == "'":
            j = s.index("'", i + 1)
            current += s[i + 1:j]
            i = j + 1
        elif c == '"':
            j = s.index('"', i + 1)
            current += s[i + 1:j]
            i = j + 1
        elif c == "\\":
            if i + 1 < len(s):
                current += s[i + 1]
                i += 2
            else:
                current += c
                i += 1
        else:
            current += c
            i += 1
    if current:
        tokens.append(current)
    return tokens
