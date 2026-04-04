"""
Tagged ID encoding compatible with the API's tagged_id.py format.

Produces IDs like "user_01PaGUP2rbg1XDh7Z9W1CEpd" from a UUID string.
"""

from __future__ import annotations

BASE_58_CHARS = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
VERSION = "01"
ENCODED_LENGTH = 22


def _base58_encode(n: int) -> str:
    """Encode a 128-bit unsigned integer as a fixed-length base58 string."""
    base = len(BASE_58_CHARS)
    result = [BASE_58_CHARS[0]] * ENCODED_LENGTH
    i = ENCODED_LENGTH - 1
    value = n
    while value > 0:
        rem = value % base
        result[i] = BASE_58_CHARS[rem]
        value //= base
        i -= 1
    return "".join(result)


def _uuid_to_int(uuid: str) -> int:
    """Parse a UUID string (with or without hyphens) into a 128-bit integer."""
    hex_str = uuid.replace("-", "")
    if len(hex_str) != 32:
        raise ValueError(f"Invalid UUID hex length: {len(hex_str)}")
    return int(hex_str, 16)


def to_tagged_id(tag: str, uuid: str) -> str:
    """
    Convert an account UUID to a tagged ID in the API's format.

    Args:
        tag: The tag prefix (e.g. "user", "org").
        uuid: A UUID string (with or without hyphens).

    Returns:
        Tagged ID string like "user_01PaGUP2rbg1XDh7Z9W1CEpd".
    """
    n = _uuid_to_int(uuid)
    return f"{tag}_{VERSION}{_base58_encode(n)}"
