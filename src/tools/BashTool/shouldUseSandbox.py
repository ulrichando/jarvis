"""
Determines whether a bash command should run inside a sandbox.
"""
from __future__ import annotations

from typing import Optional


def _split_command_deprecated(command: str) -> list[str]:
    """Split a command by pipes. Simple heuristic, not security-safe."""
    return [seg.strip() for seg in command.split("|")]


def should_use_sandbox(
    command: Optional[str] = None,
    dangerously_disable_sandbox: bool = False,
    sandbox_enabled: bool = False,
    unsandboxed_commands_allowed: bool = False,
    excluded_commands: Optional[list[str]] = None,
) -> bool:
    """Determine if a command should run in a sandbox.

    Args:
        command: The command to run.
        dangerously_disable_sandbox: If True and unsandboxed commands are allowed,
            skip sandbox.
        sandbox_enabled: Whether sandbox is enabled globally.
        unsandboxed_commands_allowed: Whether the user may opt out of sandbox per-command.
        excluded_commands: User-configured commands that skip sandbox.

    Returns:
        True if the command should be sandboxed, False otherwise.
    """
    if not sandbox_enabled:
        return False

    if dangerously_disable_sandbox and unsandboxed_commands_allowed:
        return False

    if not command:
        return False

    if excluded_commands and _contains_excluded_command(command, excluded_commands):
        return False

    return True


def _contains_excluded_command(
    command: str,
    excluded_commands: list[str],
) -> bool:
    """Check if command contains any user-configured excluded commands.

    NOTE: excludedCommands is a user-facing convenience feature, not a security boundary.
    """
    if not excluded_commands:
        return False

    try:
        subcommands = _split_command_deprecated(command)
    except Exception:
        subcommands = [command]

    for subcommand in subcommands:
        trimmed = subcommand.strip()
        for pattern in excluded_commands:
            # Simple prefix matching
            base = trimmed.split()[0] if trimmed.split() else ""
            if pattern.endswith(":*"):
                prefix = pattern[:-2]
                if base == prefix or trimmed.startswith(prefix + " "):
                    return True
            elif trimmed == pattern or base == pattern:
                return True

    return False
