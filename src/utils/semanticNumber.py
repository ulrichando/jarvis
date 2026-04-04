"""
Semantic number parsing -- coerces numeric string literals to numbers.
"""

from __future__ import annotations

import re
from typing import Any, Optional, Union

_NUMERIC_RE = re.compile(r"^-?\d+(\.\d+)?$")


def semantic_number(value: Any) -> Optional[Union[int, float]]:
    """
    Parse a value as a number, accepting numeric string literals like "30", "-5", "3.14".

    Tool inputs arrive as model-generated JSON. The model occasionally quotes
    numbers -- "head_limit":"30" instead of "head_limit":30.

    Args:
        value: The value to parse.

    Returns:
        Number value, or None if the input is None or not parseable.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str) and _NUMERIC_RE.match(value):
        n = float(value)
        if n == int(n):
            return int(n)
        return n
    return None
