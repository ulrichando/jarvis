"""Shell completion utilities."""

from __future__ import annotations

import shlex
from typing import Literal, Optional

MAX_SHELL_COMPLETIONS = 15
SHELL_COMPLETION_TIMEOUT_MS = 1000
COMMAND_OPERATORS = ("|", "||", "&&", ";")

ShellCompletionType = Literal["command", "variable", "file"]


def get_completion_type_from_prefix(prefix: str) -> ShellCompletionType:
    """Determine completion type based on prefix characteristics."""
    if prefix.startswith("$"):
        return "variable"
    if "/" in prefix or prefix.startswith("~") or prefix.startswith("."):
        return "file"
    return "command"
