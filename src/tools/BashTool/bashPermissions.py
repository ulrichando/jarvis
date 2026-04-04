"""
Bash command permission rules and matching.

Handles permission rule parsing, wildcard matching, and environment variable stripping
for bash command security validation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Optional, Union


# Environment variables that can be used to hijack binary paths
BINARY_HIJACK_VARS = frozenset([
    "PATH", "LD_PRELOAD", "LD_LIBRARY_PATH", "DYLD_INSERT_LIBRARIES",
    "DYLD_LIBRARY_PATH", "PYTHONPATH", "NODE_PATH", "RUBYLIB",
    "PERL5LIB", "CLASSPATH",
])

# Commands that wrap other commands (e.g., timeout, nice, env)
SAFE_WRAPPERS = frozenset([
    "timeout", "nice", "ionice", "strace", "ltrace", "time",
    "env", "nohup", "setsid",
])


@dataclass
class PrefixRule:
    type: Literal["prefix"] = "prefix"
    prefix: str = ""


@dataclass
class ExactRule:
    type: Literal["exact"] = "exact"
    command: str = ""


@dataclass
class WildcardRule:
    type: Literal["wildcard"] = "wildcard"
    pattern: str = ""


BashPermissionRule = Union[PrefixRule, ExactRule, WildcardRule]


def bash_permission_rule(pattern: str) -> BashPermissionRule:
    """Parse a permission rule pattern into a typed rule."""
    if pattern.endswith(":*"):
        return PrefixRule(prefix=pattern[:-2])
    elif "*" in pattern or "?" in pattern:
        return WildcardRule(pattern=pattern)
    else:
        return ExactRule(command=pattern)


def match_wildcard_pattern(pattern: str, command: str) -> bool:
    """Match a command against a wildcard pattern."""
    regex = "^" + re.escape(pattern).replace(r"\*", ".*").replace(r"\?", ".") + "$"
    return bool(re.match(regex, command))


def strip_all_leading_env_vars(
    command: str,
    hijack_vars: frozenset[str] = BINARY_HIJACK_VARS,
) -> str:
    """Strip leading environment variable assignments from a command.
    Only strips vars that are in the hijack_vars set.
    """
    result = command
    while True:
        match = re.match(r"^(\w+)=(\S+)\s+(.*)", result)
        if not match:
            break
        var_name = match.group(1)
        if var_name not in hijack_vars:
            break
        result = match.group(3)
    return result


def strip_safe_wrappers(command: str) -> str:
    """Strip safe wrapper commands from the beginning of a command."""
    result = command.strip()
    changed = True
    while changed:
        changed = False
        for wrapper in SAFE_WRAPPERS:
            # Match "wrapper" followed by optional flags and arguments
            pattern = rf"^{re.escape(wrapper)}\s+(?:-\S+\s+)*"
            match = re.match(pattern, result)
            if match:
                result = result[match.end():]
                changed = True
                break
    return result
