"""Heredoc extraction and restoration utilities."""

from __future__ import annotations

import os
import re
import secrets

HEREDOC_PLACEHOLDER_PREFIX = "__HEREDOC_"
HEREDOC_PLACEHOLDER_SUFFIX = "__"


def _generate_placeholder_salt() -> str:
    return secrets.token_hex(8)


def extract_heredocs(command: str) -> tuple[str, dict[str, str]]:
    """Extract heredocs from a command, replacing them with placeholders.

    Returns (modified_command, placeholder_map).
    """
    heredoc_re = re.compile(r"<<-?\s*(?:(['\"]?)(\w+)\1|\\(\w+))")
    salt = _generate_placeholder_salt()
    placeholders: dict[str, str] = {}
    counter = 0

    def replace_heredoc(match: re.Match) -> str:
        nonlocal counter
        placeholder = f"{HEREDOC_PLACEHOLDER_PREFIX}{salt}_{counter}{HEREDOC_PLACEHOLDER_SUFFIX}"
        placeholders[placeholder] = match.group(0)
        counter += 1
        return placeholder

    modified = heredoc_re.sub(replace_heredoc, command)
    return modified, placeholders


def restore_heredocs(command: str, placeholders: dict[str, str]) -> str:
    """Restore heredoc placeholders back to their original text."""
    result = command
    for placeholder, original in placeholders.items():
        result = result.replace(placeholder, original)
    return result
