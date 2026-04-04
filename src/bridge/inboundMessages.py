"""Process inbound user messages from the bridge."""

from __future__ import annotations

from typing import Any, Optional


def extract_inbound_message_fields(
    msg: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """Extract content and UUID from an inbound bridge user message.

    Returns the extracted fields, or None if the message should be skipped.
    """
    if msg.get("type") != "user":
        return None
    message = msg.get("message", {})
    content = message.get("content")
    if not content:
        return None
    if isinstance(content, list) and len(content) == 0:
        return None

    # Normalize image blocks that may use camelCase mediaType
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "image":
                source = block.get("source", {})
                if "mediaType" in source and "media_type" not in source:
                    source["media_type"] = source.pop("mediaType")
                # Detect format from base64 if media_type is missing
                if source.get("type") == "base64" and not source.get("media_type"):
                    data = source.get("data", "")
                    if data.startswith("/9j/"):
                        source["media_type"] = "image/jpeg"
                    elif data.startswith("iVBOR"):
                        source["media_type"] = "image/png"
                    elif data.startswith("R0lG"):
                        source["media_type"] = "image/gif"
                    elif data.startswith("UklG"):
                        source["media_type"] = "image/webp"

    uuid_val = msg.get("uuid") if isinstance(msg.get("uuid"), str) else None
    return {"content": content, "uuid": uuid_val}
