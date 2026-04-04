"""Safe shell quoting utilities."""

from __future__ import annotations

import logging
import shlex
from dataclasses import dataclass
from typing import Any, Optional, Union

logger = logging.getLogger(__name__)


@dataclass
class ShellParseSuccess:
    success: bool = True
    tokens: list[str] = None  # type: ignore

    def __post_init__(self):
        if self.tokens is None:
            self.tokens = []


@dataclass
class ShellParseFailure:
    success: bool = False
    error: str = ""


ShellParseResult = Union[ShellParseSuccess, ShellParseFailure]


@dataclass
class ShellQuoteSuccess:
    success: bool = True
    quoted: str = ""


@dataclass
class ShellQuoteFailure:
    success: bool = False
    error: str = ""


ShellQuoteResult = Union[ShellQuoteSuccess, ShellQuoteFailure]


def try_parse_shell_command(cmd: str) -> ShellParseResult:
    """Parse a shell command string into tokens."""
    try:
        tokens = shlex.split(cmd)
        return ShellParseSuccess(tokens=tokens)
    except ValueError as e:
        logger.error(f"Shell parse error: {e}")
        return ShellParseFailure(error=str(e))


def try_quote_shell_args(args: list[Any]) -> ShellQuoteResult:
    """Quote shell arguments safely."""
    try:
        validated = []
        for i, arg in enumerate(args):
            if arg is None:
                validated.append("None")
            elif isinstance(arg, (str, int, float, bool)):
                validated.append(str(arg))
            else:
                raise ValueError(
                    f"Cannot quote argument at index {i}: unsupported type {type(arg).__name__}"
                )
        quoted = " ".join(shlex.quote(a) for a in validated)
        return ShellQuoteSuccess(quoted=quoted)
    except Exception as e:
        logger.error(f"Shell quote error: {e}")
        return ShellQuoteFailure(error=str(e))


def has_malformed_tokens(command: str, parsed: list[str]) -> bool:
    """Check if parsed tokens contain malformed entries."""
    in_single = False
    in_double = False
    double_count = 0
    single_count = 0

    i = 0
    while i < len(command):
        c = command[i]
        if c == "\\" and not in_single:
            i += 2
            continue
        if c == '"' and not in_single:
            double_count += 1
            in_double = not in_double
        elif c == "'" and not in_double:
            single_count += 1
            in_single = not in_single
        i += 1

    if double_count % 2 != 0 or single_count % 2 != 0:
        return True

    for entry in parsed:
        if not isinstance(entry, str):
            continue
        if entry.count("{") != entry.count("}"):
            return True
        if entry.count("(") != entry.count(")"):
            return True
        if entry.count("[") != entry.count("]"):
            return True

    return False


def quote(args: list[Any]) -> str:
    """Quote shell arguments, with fallback for complex types."""
    result = try_quote_shell_args(args)
    if isinstance(result, ShellQuoteSuccess):
        return result.quoted

    # Fallback
    try:
        string_args = []
        for arg in args:
            if arg is None:
                string_args.append("None")
            elif isinstance(arg, (str, int, float, bool)):
                string_args.append(str(arg))
            else:
                import json
                string_args.append(json.dumps(arg))
        return " ".join(shlex.quote(a) for a in string_args)
    except Exception as e:
        raise RuntimeError("Failed to quote shell arguments safely") from e
