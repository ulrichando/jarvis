"""
Token budget parsing from user messages.
"""

from __future__ import annotations

import re
from typing import Optional

SHORTHAND_START_RE = re.compile(r"^\s*\+(\d+(?:\.\d+)?)\s*(k|m|b)\b", re.IGNORECASE)
SHORTHAND_END_RE = re.compile(r"\s\+(\d+(?:\.\d+)?)\s*(k|m|b)\s*[.!?]?\s*$", re.IGNORECASE)
VERBOSE_RE = re.compile(
    r"\b(?:use|spend)\s+(\d+(?:\.\d+)?)\s*(k|m|b)\s*tokens?\b", re.IGNORECASE
)

MULTIPLIERS = {
    "k": 1_000,
    "m": 1_000_000,
    "b": 1_000_000_000,
}


def _parse_budget_match(value: str, suffix: str) -> int:
    return int(float(value) * MULTIPLIERS[suffix.lower()])


def parse_token_budget(text: str) -> Optional[int]:
    """Parse a token budget from text like '+500k' or 'use 2M tokens'."""
    start_match = SHORTHAND_START_RE.search(text)
    if start_match:
        return _parse_budget_match(start_match.group(1), start_match.group(2))

    end_match = SHORTHAND_END_RE.search(text)
    if end_match:
        return _parse_budget_match(end_match.group(1), end_match.group(2))

    verbose_match = VERBOSE_RE.search(text)
    if verbose_match:
        return _parse_budget_match(verbose_match.group(1), verbose_match.group(2))

    return None
