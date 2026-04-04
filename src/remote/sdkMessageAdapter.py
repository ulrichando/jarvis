"""SDK message adapter for translating between internal and SDK formats."""

from __future__ import annotations

from typing import Any, Optional


def to_sdk_message(internal_msg: dict[str, Any]) -> dict[str, Any]:
    """Convert an internal message to SDK format."""
    return {
        "type": internal_msg.get("type", ""),
        "message": internal_msg.get("message", {}),
        "uuid": internal_msg.get("uuid"),
    }


def from_sdk_message(sdk_msg: dict[str, Any]) -> dict[str, Any]:
    """Convert an SDK message to internal format."""
    return {
        "type": sdk_msg.get("type", ""),
        "message": sdk_msg.get("message", {}),
        "uuid": sdk_msg.get("uuid"),
    }
