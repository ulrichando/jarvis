"""Critical system constants extracted to break circular dependencies."""

from __future__ import annotations

import os
from typing import FrozenSet, Optional, Set

DEFAULT_PREFIX = "You are JARVIS, an autonomous AI assistant built by Ulrich."
AGENT_SDK_CLAUDE_CODE_PRESET_PREFIX = (
    "You are JARVIS, an autonomous AI assistant built by Ulrich, "
    "running within the JARVIS Agent SDK."
)
AGENT_SDK_PREFIX = "You are a JARVIS agent, built on the JARVIS Agent SDK."

CLISyspromptPrefix = str  # Type alias

CLI_SYSPROMPT_PREFIXES: FrozenSet[str] = frozenset({
    DEFAULT_PREFIX,
    AGENT_SDK_CLAUDE_CODE_PRESET_PREFIX,
    AGENT_SDK_PREFIX,
})


def get_cli_sysprompt_prefix(
    is_non_interactive: bool = False,
    has_append_system_prompt: bool = False,
) -> str:
    """Get the CLI system prompt prefix based on context."""
    if is_non_interactive:
        if has_append_system_prompt:
            return AGENT_SDK_CLAUDE_CODE_PRESET_PREFIX
        return AGENT_SDK_PREFIX
    return DEFAULT_PREFIX


def get_attribution_header(fingerprint: str) -> str:
    """Get attribution header for API requests.

    Returns a header string with cc_version and cc_entrypoint.
    """
    version_str = os.environ.get("JARVIS_VERSION", "dev")
    version = f"{version_str}.{fingerprint}"
    entrypoint = os.environ.get("CLAUDE_CODE_ENTRYPOINT", "unknown")

    header = (
        f"x-anthropic-billing-header: cc_version={version}; "
        f"cc_entrypoint={entrypoint};"
    )
    return header
