"""Normalize camelCase to snake_case on incoming control messages."""

from __future__ import annotations

from typing import Any


def normalize_control_message_keys(obj: Any) -> Any:
    """Normalize requestId -> request_id on control messages.

    Mutates the object in place. If both request_id and requestId
    are present, snake_case wins.
    """
    if obj is None or not isinstance(obj, dict):
        return obj

    if "requestId" in obj and "request_id" not in obj:
        obj["request_id"] = obj.pop("requestId")

    if "response" in obj and isinstance(obj["response"], dict):
        response = obj["response"]
        if "requestId" in response and "request_id" not in response:
            response["request_id"] = response.pop("requestId")

    return obj
