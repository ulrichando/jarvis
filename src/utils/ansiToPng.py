"""ANSI to PNG conversion utilities."""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


async def ansi_to_png(
    ansi_text: str,
    output_path: str,
    width: int = 800,
    font_size: int = 14,
) -> Optional[str]:
    """Convert ANSI-colored text to a PNG image.

    Returns the output path on success, None on failure.
    """
    try:
        # Strip ANSI codes for plain text fallback
        import re
        plain = re.sub(r'\x1b\[[0-9;]*m', '', ansi_text)

        # Try using Pillow if available
        try:
            from PIL import Image, ImageDraw, ImageFont

            lines = plain.split('\n')
            line_height = font_size + 4
            img_width = width
            img_height = len(lines) * line_height + 20

            img = Image.new('RGB', (img_width, img_height), color=(30, 30, 30))
            draw = ImageDraw.Draw(img)

            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", font_size)
            except (OSError, IOError):
                font = ImageFont.load_default()

            y = 10
            for line in lines:
                draw.text((10, y), line, fill=(204, 204, 204), font=font)
                y += line_height

            img.save(output_path, 'PNG')
            return output_path
        except ImportError:
            logger.debug("Pillow not available for ANSI to PNG conversion")

        # Fallback: write plain text
        with open(output_path.replace('.png', '.txt'), 'w') as f:
            f.write(plain)
        return None

    except Exception as e:
        logger.error(f"Failed to convert ANSI to PNG: {e}")
        return None
