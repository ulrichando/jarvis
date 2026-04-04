"""
JSON and JSONL parsing utilities.
"""

from __future__ import annotations

import json
from typing import Any, Optional, TypeVar

T = TypeVar("T")


def _strip_bom(s: str) -> str:
    """Strip UTF-8 BOM from a string."""
    if s.startswith("\ufeff"):
        return s[1:]
    return s


def safe_parse_json(
    json_str: Optional[str], should_log_error: bool = True
) -> Any:
    """
    Safely parse a JSON string, returning None on failure.

    Args:
        json_str: JSON string to parse.
        should_log_error: Whether to log parsing errors.

    Returns:
        Parsed JSON value, or None on failure.
    """
    if not json_str:
        return None
    try:
        return json.loads(_strip_bom(json_str))
    except (json.JSONDecodeError, ValueError):
        return None


def parse_jsonl(data: str | bytes) -> list[Any]:
    """
    Parse JSONL data from a string or bytes, skipping malformed lines.

    Args:
        data: JSONL string or bytes.

    Returns:
        List of parsed JSON values.
    """
    if isinstance(data, bytes):
        text = data.decode("utf-8", errors="replace")
    else:
        text = data

    text = _strip_bom(text)
    results: list[Any] = []

    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            results.append(json.loads(line))
        except (json.JSONDecodeError, ValueError):
            continue

    return results


def read_jsonl_file(file_path: str) -> list[Any]:
    """
    Read and parse a JSONL file.

    Args:
        file_path: Path to the JSONL file.

    Returns:
        List of parsed JSON values.
    """
    with open(file_path, "rb") as f:
        data = f.read()
    return parse_jsonl(data)


def add_item_to_json_array(content: str, new_item: Any) -> str:
    """
    Add an item to a JSON array string, returning the modified JSON string.

    Args:
        content: JSON string containing an array.
        new_item: Item to add to the array.

    Returns:
        Modified JSON string.
    """
    if not content or not content.strip():
        return json.dumps([new_item], indent=4)

    try:
        parsed = json.loads(_strip_bom(content))
        if isinstance(parsed, list):
            parsed.append(new_item)
            return json.dumps(parsed, indent=4)
        return json.dumps([new_item], indent=4)
    except (json.JSONDecodeError, ValueError):
        return json.dumps([new_item], indent=4)
