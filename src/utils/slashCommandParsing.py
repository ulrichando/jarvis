"""
Centralized utilities for parsing slash commands.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class ParsedSlashCommand:
    """Result of parsing a slash command string."""

    command_name: str
    args: str
    is_mcp: bool


def parse_slash_command(input_str: str) -> Optional[ParsedSlashCommand]:
    """
    Parse a slash command input string into its component parts.

    Args:
        input_str: The raw input string (should start with '/').

    Returns:
        ParsedSlashCommand with command_name, args, and is_mcp flag,
        or None if the input is invalid.

    Examples:
        >>> parse_slash_command('/search foo bar')
        ParsedSlashCommand(command_name='search', args='foo bar', is_mcp=False)

        >>> parse_slash_command('/mcp:tool (MCP) arg1 arg2')
        ParsedSlashCommand(command_name='mcp:tool (MCP)', args='arg1 arg2', is_mcp=True)
    """
    trimmed = input_str.strip()

    if not trimmed.startswith("/"):
        return None

    without_slash = trimmed[1:]
    words = without_slash.split(" ")

    if not words[0]:
        return None

    command_name = words[0]
    is_mcp = False
    args_start_index = 1

    # Check for MCP commands (second word is '(MCP)')
    if len(words) > 1 and words[1] == "(MCP)":
        command_name = command_name + " (MCP)"
        is_mcp = True
        args_start_index = 2

    args = " ".join(words[args_start_index:])

    return ParsedSlashCommand(
        command_name=command_name,
        args=args,
        is_mcp=is_mcp,
    )
