"""Attachment handling for the BriefTool."""
from __future__ import annotations

from typing import Any, Optional


def process_attachments(
    attachment_paths: list[str],
) -> list[dict[str, Any]]:
    """Process file paths into attachment metadata."""
    attachments = []
    for path in attachment_paths:
        attachments.append({
            "path": path,
            "type": "file",
        })
    return attachments
