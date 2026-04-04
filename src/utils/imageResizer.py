"""
Image resizing and compression utilities.

Provides image resizing and compression to meet API size and dimension
constraints. Uses Pillow when available, with fallbacks for when
image processing fails.
"""

from __future__ import annotations

import base64
import io
import logging
from dataclasses import dataclass
from typing import Literal, Optional

logger = logging.getLogger(__name__)

try:
    from PIL import Image

    _HAS_PILLOW = True
except ImportError:
    _HAS_PILLOW = False

ImageMediaType = Literal["image/png", "image/jpeg", "image/gif", "image/webp"]

# Constants
IMAGE_MAX_WIDTH = 8000
IMAGE_MAX_HEIGHT = 8000
IMAGE_TARGET_RAW_SIZE = 3 * 1024 * 1024  # 3MB
API_IMAGE_MAX_BASE64_SIZE = 5 * 1024 * 1024  # 5MB


class ImageResizeError(Exception):
    """Error thrown when image resizing fails and the image exceeds the API limit."""

    pass


@dataclass
class ImageDimensions:
    """Image dimension metadata."""

    original_width: Optional[int] = None
    original_height: Optional[int] = None
    display_width: Optional[int] = None
    display_height: Optional[int] = None


@dataclass
class ResizeResult:
    """Result of an image resize operation."""

    data: bytes
    media_type: str
    dimensions: Optional[ImageDimensions] = None


def detect_image_format_from_buffer(data: bytes) -> ImageMediaType:
    """
    Detect image format from magic bytes.

    Args:
        data: Raw image bytes.

    Returns:
        The detected media type string.
    """
    if len(data) < 4:
        return "image/png"

    # PNG signature
    if data[:4] == b"\x89PNG":
        return "image/png"

    # JPEG signature (FFD8FF)
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"

    # GIF signature
    if data[:3] == b"GIF":
        return "image/gif"

    # WebP signature (RIFF....WEBP)
    if data[:4] == b"RIFF" and len(data) >= 12 and data[8:12] == b"WEBP":
        return "image/webp"

    return "image/png"


def detect_image_format_from_base64(base64_data: str) -> ImageMediaType:
    """
    Detect image format from base64 data using magic bytes.

    Args:
        base64_data: Base64 encoded image data.

    Returns:
        The detected media type string.
    """
    try:
        data = base64.b64decode(base64_data[:64])
        return detect_image_format_from_buffer(data)
    except Exception:
        return "image/png"


async def maybe_resize_and_downsample_image_buffer(
    image_buffer: bytes,
    original_size: int,
    ext: str,
) -> ResizeResult:
    """
    Resize an image buffer to meet size and dimension constraints.

    Args:
        image_buffer: Raw image bytes.
        original_size: Original file size in bytes.
        ext: File extension (e.g., 'png', 'jpg').

    Returns:
        ResizeResult with the processed image data and metadata.

    Raises:
        ImageResizeError: If the image cannot be resized and exceeds limits.
    """
    if len(image_buffer) == 0:
        raise ImageResizeError("Image file is empty (0 bytes)")

    if not _HAS_PILLOW:
        # No image processing available - check if raw size is OK
        base64_size = (original_size * 4 + 2) // 3
        if base64_size <= API_IMAGE_MAX_BASE64_SIZE:
            media_type = ext if ext != "jpg" else "jpeg"
            return ResizeResult(data=image_buffer, media_type=media_type)
        raise ImageResizeError(
            f"Image too large ({original_size} bytes) and Pillow not installed "
            f"for resizing. Install Pillow: pip install Pillow"
        )

    try:
        img = Image.open(io.BytesIO(image_buffer))
        media_type = ext if ext != "jpg" else "jpeg"
        width, height = img.size
        original_width, original_height = width, height

        # Check if image is fine as-is
        if (
            original_size <= IMAGE_TARGET_RAW_SIZE
            and width <= IMAGE_MAX_WIDTH
            and height <= IMAGE_MAX_HEIGHT
        ):
            return ResizeResult(
                data=image_buffer,
                media_type=media_type,
                dimensions=ImageDimensions(
                    original_width=original_width,
                    original_height=original_height,
                    display_width=width,
                    display_height=height,
                ),
            )

        # Constrain dimensions
        if width > IMAGE_MAX_WIDTH:
            height = round((height * IMAGE_MAX_WIDTH) / width)
            width = IMAGE_MAX_WIDTH
        if height > IMAGE_MAX_HEIGHT:
            width = round((width * IMAGE_MAX_HEIGHT) / height)
            height = IMAGE_MAX_HEIGHT

        # Resize
        img = img.resize((width, height), Image.LANCZOS)

        # Try saving as original format first
        buf = io.BytesIO()
        save_format = "PNG" if media_type == "png" else "JPEG"
        if save_format == "JPEG":
            img = img.convert("RGB")
            img.save(buf, format="JPEG", quality=80)
        else:
            img.save(buf, format="PNG", optimize=True)

        result_data = buf.getvalue()
        if len(result_data) <= IMAGE_TARGET_RAW_SIZE:
            return ResizeResult(
                data=result_data,
                media_type=media_type,
                dimensions=ImageDimensions(
                    original_width=original_width,
                    original_height=original_height,
                    display_width=width,
                    display_height=height,
                ),
            )

        # Try JPEG with lower quality
        for quality in (60, 40, 20):
            buf = io.BytesIO()
            rgb_img = img.convert("RGB")
            rgb_img.save(buf, format="JPEG", quality=quality)
            result_data = buf.getvalue()
            if len(result_data) <= IMAGE_TARGET_RAW_SIZE:
                return ResizeResult(
                    data=result_data,
                    media_type="jpeg",
                    dimensions=ImageDimensions(
                        original_width=original_width,
                        original_height=original_height,
                        display_width=width,
                        display_height=height,
                    ),
                )

        # Last resort: shrink further
        smaller_width = min(width, 1000)
        smaller_height = round((height * smaller_width) / max(width, 1))
        img = img.resize((smaller_width, smaller_height), Image.LANCZOS)
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=20)

        return ResizeResult(
            data=buf.getvalue(),
            media_type="jpeg",
            dimensions=ImageDimensions(
                original_width=original_width,
                original_height=original_height,
                display_width=smaller_width,
                display_height=smaller_height,
            ),
        )

    except Exception as e:
        logger.error(f"Image resize failed: {e}")
        detected = detect_image_format_from_buffer(image_buffer)
        base64_size = (original_size * 4 + 2) // 3

        if base64_size <= API_IMAGE_MAX_BASE64_SIZE:
            return ResizeResult(
                data=image_buffer, media_type=detected.replace("image/", "")
            )

        raise ImageResizeError(
            f"Unable to resize image ({original_size} bytes raw, "
            f"{base64_size} bytes base64). The image exceeds the 5MB API limit "
            f"and compression failed. Please resize manually or use a smaller image."
        )


def create_image_metadata_text(
    dims: ImageDimensions,
    source_path: Optional[str] = None,
) -> Optional[str]:
    """
    Create a text description of image metadata including dimensions
    and source path. Returns None if no useful metadata is available.
    """
    ow = dims.original_width
    oh = dims.original_height
    dw = dims.display_width
    dh = dims.display_height

    if not ow or not oh or not dw or not dh or dw <= 0 or dh <= 0:
        if source_path:
            return f"[Image source: {source_path}]"
        return None

    was_resized = ow != dw or oh != dh

    if not was_resized and not source_path:
        return None

    parts: list[str] = []
    if source_path:
        parts.append(f"source: {source_path}")
    if was_resized:
        scale_factor = ow / dw
        parts.append(
            f"original {ow}x{oh}, displayed at {dw}x{dh}. "
            f"Multiply coordinates by {scale_factor:.2f} to map to original image."
        )

    return f"[Image: {', '.join(parts)}]"
