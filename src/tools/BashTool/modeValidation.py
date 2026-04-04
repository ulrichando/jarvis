"""
Mode-based permission validation for bash commands.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


ACCEPT_EDITS_ALLOWED_COMMANDS = (
    "mkdir", "touch", "rm", "rmdir", "mv", "cp", "sed",
)


@dataclass
class PermissionResult:
    behavior: str  # "allow", "ask", "passthrough"
    message: Optional[str] = None
    updated_input: Optional[dict[str, Any]] = None
    decision_reason: Optional[dict[str, Any]] = None


def _split_command_deprecated(command: str) -> list[str]:
    """Split a command by pipes. Simple heuristic, not security-safe."""
    return [seg.strip() for seg in command.split("|")]


def _is_filesystem_command(command: str) -> bool:
    return command in ACCEPT_EDITS_ALLOWED_COMMANDS


def _validate_command_for_mode(
    cmd: str,
    mode: str,
) -> PermissionResult:
    trimmed_cmd = cmd.strip()
    parts = trimmed_cmd.split()
    base_cmd = parts[0] if parts else ""

    if not base_cmd:
        return PermissionResult(behavior="passthrough", message="Base command not found")

    # In Accept Edits mode, auto-allow filesystem operations
    if mode == "acceptEdits" and _is_filesystem_command(base_cmd):
        return PermissionResult(
            behavior="allow",
            updated_input={"command": cmd},
            decision_reason={"type": "mode", "mode": "acceptEdits"},
        )

    return PermissionResult(
        behavior="passthrough",
        message=f"No mode-specific handling for '{base_cmd}' in {mode} mode",
    )


def check_permission_mode(
    command: str,
    mode: str,
) -> PermissionResult:
    """Checks if commands should be handled differently based on the current permission mode.

    Returns:
        - 'allow' if the current mode permits auto-approval
        - 'ask' if the command needs approval in current mode
        - 'passthrough' if no mode-specific handling applies
    """
    if mode == "bypassPermissions":
        return PermissionResult(
            behavior="passthrough",
            message="Bypass mode is handled in main permission flow",
        )

    if mode == "dontAsk":
        return PermissionResult(
            behavior="passthrough",
            message="DontAsk mode is handled in main permission flow",
        )

    commands = _split_command_deprecated(command)

    for cmd in commands:
        result = _validate_command_for_mode(cmd, mode)
        if result.behavior != "passthrough":
            return result

    return PermissionResult(
        behavior="passthrough",
        message="No mode-specific validation required",
    )


def get_auto_allowed_commands(mode: str) -> tuple[str, ...]:
    return ACCEPT_EDITS_ALLOWED_COMMANDS if mode == "acceptEdits" else ()
