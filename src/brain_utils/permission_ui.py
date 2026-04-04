"""Permission UI utilities for JARVIS CLI display.

Formats permission requests for terminal display with risk-level color coding,
and maps permission modes to tool-level actions.

Adapted from TypeScript permission components.
"""

from __future__ import annotations

from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Permission modes
# ---------------------------------------------------------------------------

PERMISSION_MODES: dict[str, str] = {
    "default": "Ask before sensitive operations (file writes, bash commands)",
    "plan": "Read-only mode -- no writes or executions allowed",
    "trust": "Allow all tool calls without prompting",
    "deny": "Deny all tool calls unless explicitly allowlisted",
}

# Tools that are always safe in any mode (read-only, no side effects)
_ALWAYS_SAFE_TOOLS = frozenset({
    "read_file", "search_files", "web_search", "think",
})

# Tools that are write-level (file mutations, code execution)
_WRITE_TOOLS = frozenset({
    "write_file", "edit_file", "bash", "dispatch",
})


# ---------------------------------------------------------------------------
# Risk level colors (ANSI)
# ---------------------------------------------------------------------------

_RISK_COLORS: dict[str, str] = {
    "low": "\033[32m",      # green
    "medium": "\033[33m",   # yellow
    "high": "\033[31m",     # red
}
_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class PermissionRequest:
    """A single permission prompt shown to the user."""
    tool_name: str
    args_summary: str
    risk_level: str  # "low", "medium", "high"
    description: str

    def __post_init__(self):
        if self.risk_level not in ("low", "medium", "high"):
            raise ValueError(
                f"Invalid risk_level {self.risk_level!r}, "
                "must be 'low', 'medium', or 'high'"
            )


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def format_permission_request(req: PermissionRequest) -> str:
    """Format a permission request for CLI display with risk color coding.

    Returns a multi-line ANSI-colored string suitable for terminal output.

    Example output (without ANSI codes):

        [MEDIUM] bash
        Command: git push --force
        Force-pushing to remote repository
    """
    color = _RISK_COLORS.get(req.risk_level, "")
    tag = req.risk_level.upper()

    lines = [
        f"{color}{_BOLD}[{tag}]{_RESET} {_BOLD}{req.tool_name}{_RESET}",
    ]
    if req.args_summary:
        lines.append(f"  {_DIM}{req.args_summary}{_RESET}")
    if req.description:
        lines.append(f"  {req.description}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Mode -> action mapping
# ---------------------------------------------------------------------------

def check_permission_mode(mode: str, tool: str) -> str:
    """Given a permission mode and tool name, return the action to take.

    Returns:
        "allow" -- execute without prompting
        "ask"   -- prompt the user for confirmation
        "deny"  -- block the tool call
    """
    mode = mode.lower()

    if mode == "trust":
        return "allow"

    if mode == "deny":
        # In deny mode, only explicitly safe tools are allowed
        if tool in _ALWAYS_SAFE_TOOLS:
            return "allow"
        return "deny"

    if mode == "plan":
        # Plan mode: read-only tools allowed, everything else denied
        if tool in _ALWAYS_SAFE_TOOLS:
            return "allow"
        return "deny"

    # Default mode: safe tools auto-allowed, everything else asks
    if tool in _ALWAYS_SAFE_TOOLS:
        return "allow"
    return "ask"
