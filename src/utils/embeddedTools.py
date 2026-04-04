"""
Embedded search tools detection.

Checks whether this build has search tools (bfs/ugrep) embedded in the binary.
"""

from __future__ import annotations

import os
import sys


def has_embedded_search_tools() -> bool:
    """
    Whether this build has embedded search tools.

    When true:
    - find and grep in the shell are shadowed by embedded implementations
    - Dedicated Glob/Grep tools are removed from the tool registry
    - Prompt guidance steering away from find/grep is omitted
    """
    if not _is_env_truthy(os.environ.get("EMBEDDED_SEARCH_TOOLS", "")):
        return False
    entrypoint = os.environ.get("CLAUDE_CODE_ENTRYPOINT", "")
    return entrypoint not in ("sdk-ts", "sdk-py", "sdk-cli", "local-agent")


def embedded_search_tools_binary_path() -> str:
    """
    Path to the binary that contains the embedded search tools.
    Only meaningful when has_embedded_search_tools() is True.
    """
    return sys.executable


def _is_env_truthy(value: str) -> bool:
    """Check if an environment variable value is truthy."""
    return value.lower() in ("1", "true", "yes")
