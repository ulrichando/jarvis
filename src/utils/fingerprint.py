"""
Fingerprint computation for JARVIS attribution.
"""

from __future__ import annotations

import hashlib
from typing import Any

FINGERPRINT_SALT = "59cf53e54c78"


def extract_first_message_text(messages: list[dict[str, Any]]) -> str:
    """
    Extracts text content from the first user message.

    Args:
        messages: Array of message dicts with 'type' and 'message' keys.

    Returns:
        First text content, or empty string if not found.
    """
    for msg in messages:
        if msg.get("type") == "user":
            content = msg.get("message", {}).get("content", "")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        return block.get("text", "")
            return ""
    return ""


def compute_fingerprint(message_text: str, version: str) -> str:
    """
    Computes 3-character fingerprint for JARVIS attribution.
    Algorithm: SHA256(SALT + msg[4] + msg[7] + msg[20] + version)[:3]

    Args:
        message_text: First user message text content.
        version: Version string.

    Returns:
        3-character hex fingerprint.
    """
    indices = [4, 7, 20]
    chars = "".join(
        message_text[i] if i < len(message_text) else "0" for i in indices
    )

    fingerprint_input = f"{FINGERPRINT_SALT}{chars}{version}"
    hash_hex = hashlib.sha256(fingerprint_input.encode()).hexdigest()
    return hash_hex[:3]
