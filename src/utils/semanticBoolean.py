"""
Semantic boolean parsing -- coerces "true"/"false" strings to booleans.
"""

from __future__ import annotations

from typing import Any, Optional, Union


def semantic_boolean(value: Any) -> Optional[bool]:
    """
    Parse a value as a boolean, accepting string literals "true"/"false".

    Tool inputs arrive as model-generated JSON. The model occasionally quotes
    booleans -- "replace_all":"false" instead of "replace_all":false.

    Args:
        value: The value to parse.

    Returns:
        Boolean value, or None if the input is None.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if value.lower() == "true":
            return True
        if value.lower() == "false":
            return False
    return bool(value)
