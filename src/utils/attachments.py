"""Attachment handling utilities."""

from __future__ import annotations

import base64
import logging
import mimetypes
import os
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class Attachment:
    type: str  # 'image', 'text', 'document'
    content: str  # base64 for images, text for documents
    media_type: Optional[str] = None
    filename: Optional[str] = None
    source_path: Optional[str] = None


_suppress_next_skill_listing = False


def suppress_next_skill_listing() -> None:
    """Suppress the next skill listing in output."""
    global _suppress_next_skill_listing
    _suppress_next_skill_listing = True


def should_suppress_skill_listing() -> bool:
    """Check and reset the suppress flag."""
    global _suppress_next_skill_listing
    val = _suppress_next_skill_listing
    _suppress_next_skill_listing = False
    return val


async def load_attachment(file_path: str) -> Optional[Attachment]:
    """Load a file as an attachment."""
    try:
        mime_type, _ = mimetypes.guess_type(file_path)
        filename = os.path.basename(file_path)

        if mime_type and mime_type.startswith("image/"):
            with open(file_path, "rb") as f:
                content = base64.b64encode(f.read()).decode("utf-8")
            return Attachment(
                type="image",
                content=content,
                media_type=mime_type,
                filename=filename,
                source_path=file_path,
            )
        else:
            with open(file_path, "r") as f:
                content = f.read()
            return Attachment(
                type="text",
                content=content,
                media_type=mime_type or "text/plain",
                filename=filename,
                source_path=file_path,
            )
    except Exception as e:
        logger.error(f"Failed to load attachment {file_path}: {e}")
        return None
