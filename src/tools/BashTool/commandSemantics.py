"""
Command semantics configuration for interpreting exit codes in different contexts.

Many commands use exit codes to convey information other than just success/failure.
For example, grep returns 1 when no matches are found, which is not an error condition.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class CommandResult:
    is_error: bool
    message: Optional[str] = None


CommandSemantic = Callable[[int, str, str], CommandResult]


def _default_semantic(exit_code: int, _stdout: str, _stderr: str) -> CommandResult:
    """Default semantic: treat only 0 as success, everything else as error."""
    return CommandResult(
        is_error=exit_code != 0,
        message=f"Command failed with exit code {exit_code}" if exit_code != 0 else None,
    )


def _grep_semantic(exit_code: int, _stdout: str, _stderr: str) -> CommandResult:
    """grep: 0=matches found, 1=no matches, 2+=error."""
    return CommandResult(
        is_error=exit_code >= 2,
        message="No matches found" if exit_code == 1 else None,
    )


def _find_semantic(exit_code: int, _stdout: str, _stderr: str) -> CommandResult:
    """find: 0=success, 1=partial success (some dirs inaccessible), 2+=error."""
    return CommandResult(
        is_error=exit_code >= 2,
        message="Some directories were inaccessible" if exit_code == 1 else None,
    )


def _diff_semantic(exit_code: int, _stdout: str, _stderr: str) -> CommandResult:
    """diff: 0=no differences, 1=differences found, 2+=error."""
    return CommandResult(
        is_error=exit_code >= 2,
        message="Files differ" if exit_code == 1 else None,
    )


def _test_semantic(exit_code: int, _stdout: str, _stderr: str) -> CommandResult:
    """test/[: 0=condition true, 1=condition false, 2+=error."""
    return CommandResult(
        is_error=exit_code >= 2,
        message="Condition is false" if exit_code == 1 else None,
    )


COMMAND_SEMANTICS: dict[str, CommandSemantic] = {
    "grep": _grep_semantic,
    "rg": _grep_semantic,
    "find": _find_semantic,
    "diff": _diff_semantic,
    "test": _test_semantic,
    "[": _test_semantic,
}


def _split_command_deprecated(command: str) -> list[str]:
    """Split a command by pipes. Simple heuristic, not security-safe."""
    return [seg.strip() for seg in command.split("|")]


def _extract_base_command(command: str) -> str:
    """Extract just the command name (first word) from a single command string."""
    parts = command.strip().split()
    return parts[0] if parts else ""


def _heuristically_extract_base_command(command: str) -> str:
    """Extract the primary command from a complex command line.
    May get it super wrong -- don't depend on this for security.
    """
    segments = _split_command_deprecated(command)
    last_command = segments[-1] if segments else command
    return _extract_base_command(last_command)


def _get_command_semantic(command: str) -> CommandSemantic:
    """Get the semantic interpretation for a command."""
    base_command = _heuristically_extract_base_command(command)
    return COMMAND_SEMANTICS.get(base_command, _default_semantic)


def interpret_command_result(
    command: str,
    exit_code: int,
    stdout: str,
    stderr: str,
) -> CommandResult:
    """Interpret command result based on semantic rules."""
    semantic = _get_command_semantic(command)
    return semantic(exit_code, stdout, stderr)
