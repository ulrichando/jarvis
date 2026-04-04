"""
Semver comparison utilities.
"""

from __future__ import annotations

import re
from typing import Literal, Optional


def _parse_semver(version: str) -> tuple[int, int, int, str]:
    """Parse a semver string into (major, minor, patch, prerelease)."""
    # Strip leading 'v'
    v = version.lstrip("v")
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:-(.+))?", v)
    if not match:
        # Loose: try to extract numbers
        parts = re.findall(r"\d+", v)
        if len(parts) >= 3:
            return int(parts[0]), int(parts[1]), int(parts[2]), ""
        raise ValueError(f"Invalid semver: {version}")
    return (
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3)),
        match.group(4) or "",
    )


def _compare(a: str, b: str) -> int:
    """Compare two semver strings. Returns -1, 0, or 1."""
    va = _parse_semver(a)
    vb = _parse_semver(b)
    for i in range(3):
        if va[i] < vb[i]:
            return -1
        if va[i] > vb[i]:
            return 1
    # Prerelease handling: no prerelease > has prerelease
    if not va[3] and vb[3]:
        return 1
    if va[3] and not vb[3]:
        return -1
    if va[3] < vb[3]:
        return -1
    if va[3] > vb[3]:
        return 1
    return 0


def gt(a: str, b: str) -> bool:
    return _compare(a, b) == 1


def gte(a: str, b: str) -> bool:
    return _compare(a, b) >= 0


def lt(a: str, b: str) -> bool:
    return _compare(a, b) == -1


def lte(a: str, b: str) -> bool:
    return _compare(a, b) <= 0


def order(a: str, b: str) -> Literal[-1, 0, 1]:
    result = _compare(a, b)
    if result < 0:
        return -1
    if result > 0:
        return 1
    return 0


def satisfies(version: str, range_str: str) -> bool:
    """Basic semver range satisfaction check. Supports ^, ~, >=, <=, >, <, =."""
    version_tuple = _parse_semver(version)

    # Handle caret ranges: ^1.2.3
    if range_str.startswith("^"):
        base = _parse_semver(range_str[1:])
        if version_tuple[0] != base[0]:
            return False
        return (version_tuple[1], version_tuple[2]) >= (base[1], base[2])

    # Handle tilde ranges: ~1.2.3
    if range_str.startswith("~"):
        base = _parse_semver(range_str[1:])
        if version_tuple[0] != base[0] or version_tuple[1] != base[1]:
            return False
        return version_tuple[2] >= base[2]

    # Handle comparison operators
    if range_str.startswith(">="):
        return gte(version, range_str[2:])
    if range_str.startswith("<="):
        return lte(version, range_str[2:])
    if range_str.startswith(">"):
        return gt(version, range_str[1:])
    if range_str.startswith("<"):
        return lt(version, range_str[1:])
    if range_str.startswith("="):
        return _compare(version, range_str[1:]) == 0

    # Exact match
    return _compare(version, range_str) == 0
