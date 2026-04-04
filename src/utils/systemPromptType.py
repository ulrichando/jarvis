"""
Branded type for system prompt arrays.

This module is intentionally dependency-free so it can be imported
from anywhere without risking circular initialization issues.
"""

from __future__ import annotations

from typing import List, Sequence, Tuple

# In Python, we represent SystemPrompt as a tuple of strings (immutable).
# The TypeScript version uses a branded readonly string array.
SystemPrompt = Tuple[str, ...]


def as_system_prompt(value: Sequence[str]) -> SystemPrompt:
    """Convert a sequence of strings into a SystemPrompt tuple."""
    return tuple(value)
