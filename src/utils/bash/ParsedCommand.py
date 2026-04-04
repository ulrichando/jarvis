"""Parsed command interface and implementations."""

from __future__ import annotations

import shlex
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class OutputRedirection:
    target: str
    operator: str  # '>' or '>>'


class IParsedCommand(ABC):
    """Interface for parsed command implementations."""

    @property
    @abstractmethod
    def original_command(self) -> str:
        ...

    @abstractmethod
    def get_pipe_segments(self) -> list[str]:
        ...

    @abstractmethod
    def without_output_redirections(self) -> str:
        ...

    @abstractmethod
    def get_output_redirections(self) -> list[OutputRedirection]:
        ...


class RegexParsedCommand(IParsedCommand):
    """Regex-based fallback implementation using shlex."""

    def __init__(self, command: str):
        self._command = command

    @property
    def original_command(self) -> str:
        return self._command

    def __str__(self) -> str:
        return self._command

    def get_pipe_segments(self) -> list[str]:
        """Split command by pipe operators (simple approach)."""
        # Simple pipe splitting - doesn't handle pipes inside quotes
        segments = []
        current = []
        in_single = False
        in_double = False

        for char in self._command:
            if char == "'" and not in_double:
                in_single = not in_single
                current.append(char)
            elif char == '"' and not in_single:
                in_double = not in_double
                current.append(char)
            elif char == "|" and not in_single and not in_double:
                seg = "".join(current).strip()
                if seg:
                    segments.append(seg)
                current = []
            else:
                current.append(char)

        remaining = "".join(current).strip()
        if remaining:
            segments.append(remaining)

        return segments if segments else [self._command]

    def without_output_redirections(self) -> str:
        """Remove output redirections from command."""
        if ">" not in self._command:
            return self._command
        # Simple redirection removal
        import re
        cleaned = re.sub(r'\s*>{1,2}\s*\S+', '', self._command)
        return cleaned.strip()

    def get_output_redirections(self) -> list[OutputRedirection]:
        """Extract output redirections."""
        import re
        redirections = []
        for m in re.finditer(r'(>>?)\s*(\S+)', self._command):
            redirections.append(OutputRedirection(
                target=m.group(2),
                operator=m.group(1),
            ))
        return redirections


_last_cmd: Optional[str] = None
_last_result: Optional[IParsedCommand] = None


async def parse_command(command: str) -> Optional[IParsedCommand]:
    """Parse a command string and return a ParsedCommand instance."""
    global _last_cmd, _last_result

    if not command:
        return None

    if command == _last_cmd and _last_result is not None:
        return _last_result

    _last_cmd = command
    _last_result = RegexParsedCommand(command)
    return _last_result
