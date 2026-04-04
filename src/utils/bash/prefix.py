"""Command prefix extraction for bash commands."""

from __future__ import annotations

import re
import shlex
from typing import Optional

NUMERIC = re.compile(r"^\d+$")
ENV_VAR = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

WRAPPER_COMMANDS = {"nice"}


async def get_command_prefix_static(
    command: str,
    recursion_depth: int = 0,
    wrapper_count: int = 0,
) -> Optional[dict]:
    """Extract the command prefix from a bash command string."""
    if wrapper_count > 2 or recursion_depth > 10:
        return None

    try:
        tokens = shlex.split(command)
    except ValueError:
        return None

    if not tokens:
        return {"commandPrefix": None}

    # Skip env var assignments
    cmd_idx = 0
    while cmd_idx < len(tokens) and ENV_VAR.match(tokens[cmd_idx]):
        cmd_idx += 1

    if cmd_idx >= len(tokens):
        return {"commandPrefix": None}

    return {"commandPrefix": tokens[cmd_idx]}
