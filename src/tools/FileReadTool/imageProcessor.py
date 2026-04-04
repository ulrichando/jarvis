"""
Image processing utilities for the FileReadTool.
Uses PIL/Pillow for image resizing and format conversion.
"""
from __future__ import annotations

import io
from typing import Optional

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


async def get_image_processor():
    """Get a PIL-based image processor (or raise if not available)."""
    if not HAS_PIL:
        raise ImportError(
            "Pillow is required for image processing. Install with: pip install Pillow"
        )
    return Image


async def resize_image(
    image_data: bytes,
    max_width: int = 1568,
    max_height: int = 1568,
    quality: int = 85,
) -> tuple[bytes, str]:
    """Resize an image to fit within the specified dimensions.

    Returns:
        Tuple of (resized image bytes, media type string like 'jpeg').
    """
    if not HAS_PIL:
        return image_data, "png"

    img = Image.open(io.BytesIO(image_data))
    width, height = img.size

    if width > max_width or height > max_height:
        ratio = min(max_width / width, max_height / height)
        new_width = int(width * ratio)
        new_height = int(height * ratio)
        img = img.resize((new_width, new_height), Image.LANCZOS)

    output = io.BytesIO()
    fmt = img.format or "PNG"
    if fmt.upper() == "JPEG":
        img.save(output, format="JPEG", quality=quality)
        media_type = "jpeg"
    else:
        img.save(output, format="PNG")
        media_type = "png"

    return output.getvalue(), media_type
