"""
Hash utilities -- fast non-cryptographic and content hashing.
"""

from __future__ import annotations

import hashlib


def djb2_hash(s: str) -> int:
    """
    djb2 string hash -- fast non-cryptographic hash returning a signed 32-bit int.
    Deterministic across runtimes.
    """
    h = 0
    for ch in s:
        h = ((h << 5) - h + ord(ch)) & 0xFFFFFFFF
    # Convert to signed 32-bit
    if h >= 0x80000000:
        h -= 0x100000000
    return h


def hash_content(content: str) -> str:
    """Hash arbitrary content for change detection."""
    return hashlib.sha256(content.encode()).hexdigest()


def hash_pair(a: str, b: str) -> str:
    """
    Hash two strings without allocating a concatenated temp string.
    Uses incremental SHA-256 update with a null separator.
    """
    h = hashlib.sha256()
    h.update(a.encode())
    h.update(b"\0")
    h.update(b.encode())
    return h.hexdigest()
