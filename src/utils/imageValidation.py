"""
Image validation utilities for API submissions.

Validates that images in messages don't exceed size limits before
sending to the API.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

# Default API image max base64 size (5MB)
API_IMAGE_MAX_BASE64_SIZE = 5 * 1024 * 1024


@dataclass
class OversizedImage:
    """Information about an image that exceeds the size limit."""

    index: int
    size: int


class ImageSizeError(Exception):
    """Error thrown when one or more images exceed the API size limit."""

    def __init__(self, oversized_images: list[OversizedImage], max_size: int) -> None:
        if len(oversized_images) == 1:
            img = oversized_images[0]
            message = (
                f"Image base64 size ({_format_size(img.size)}) exceeds API limit "
                f"({_format_size(max_size)}). Please resize the image before sending."
            )
        else:
            details = ", ".join(
                f"Image {img.index}: {_format_size(img.size)}"
                for img in oversized_images
            )
            message = (
                f"{len(oversized_images)} images exceed the API limit "
                f"({_format_size(max_size)}): {details}. "
                f"Please resize these images before sending."
            )
        super().__init__(message)
        self.name = "ImageSizeError"
        self.oversized_images = oversized_images


def _format_size(size: int) -> str:
    """Format a byte size as a human-readable string."""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def _is_base64_image_block(block: Any) -> bool:
    """Type guard to check if a block is a base64 image block."""
    if not isinstance(block, dict):
        return False
    if block.get("type") != "image":
        return False
    source = block.get("source")
    if not isinstance(source, dict):
        return False
    return source.get("type") == "base64" and isinstance(source.get("data"), str)


def validate_images_for_api(
    messages: list[Any],
    max_size: int = API_IMAGE_MAX_BASE64_SIZE,
) -> None:
    """
    Validate that all images in messages are within the API size limit.

    This is a safety net at the API boundary to catch any oversized images
    that may have slipped through upstream processing.

    The API's 5MB limit applies to the base64-encoded string length,
    not the decoded raw bytes.

    Args:
        messages: Array of messages to validate.
        max_size: Maximum allowed base64 string length.

    Raises:
        ImageSizeError: If any image exceeds the API limit.
    """
    oversized_images: list[OversizedImage] = []
    image_index = 0

    for msg in messages:
        if not isinstance(msg, dict):
            continue

        # Only check user messages
        if msg.get("type") != "user":
            continue

        inner_message = msg.get("message")
        if not isinstance(inner_message, dict):
            continue

        content = inner_message.get("content")
        if isinstance(content, str) or not isinstance(content, list):
            continue

        for block in content:
            if _is_base64_image_block(block):
                image_index += 1
                base64_size = len(block["source"]["data"])
                if base64_size > max_size:
                    oversized_images.append(
                        OversizedImage(index=image_index, size=base64_size)
                    )

    if oversized_images:
        raise ImageSizeError(oversized_images, max_size)
