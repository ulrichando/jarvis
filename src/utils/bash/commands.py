"""Bash command parsing and splitting utilities."""

from __future__ import annotations

import re
import shlex
from typing import Optional


ALLOWED_FILE_DESCRIPTORS = {"0", "1", "2"}


def split_command_with_operators(command: str) -> list[str]:
    """Split a command into tokens preserving operators."""
    try:
        return shlex.split(command)
    except ValueError:
        return [command]


def extract_output_redirections(
    command: str,
) -> dict:
    """Extract output redirections from a command.

    Returns dict with 'commandWithoutRedirections' and 'redirections' keys.
    """
    pattern = re.compile(r'\s*(>>?)\s*(\S+)')
    redirections = []
    for m in pattern.finditer(command):
        redirections.append({
            "target": m.group(2),
            "operator": m.group(1),
        })

    cleaned = pattern.sub("", command).strip()
    return {
        "commandWithoutRedirections": cleaned,
        "redirections": redirections,
    }
