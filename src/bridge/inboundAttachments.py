"""Resolve file_uuid attachments on inbound bridge user messages."""

from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path
from typing import Any, Optional

import aiohttp

logger = logging.getLogger(__name__)

DOWNLOAD_TIMEOUT_MS = 30_000


async def resolve_file_attachments(
    content_blocks: list[dict[str, Any]],
    session_id: str,
    access_token: Optional[str] = None,
    base_url: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Resolve file_uuid attachments, downloading and saving locally.

    Returns modified content blocks with @path refs prepended.
    Best-effort: any failure logs and skips that attachment.
    """
    resolved: list[dict[str, Any]] = []
    upload_dir = os.path.join(
        os.environ.get("JARVIS_HOME", os.path.expanduser("~/.jarvis")),
        "uploads",
        session_id,
    )

    for block in content_blocks:
        if block.get("type") != "file_uuid":
            resolved.append(block)
            continue

        file_uuid = block.get("file_uuid")
        filename = block.get("filename", f"{uuid.uuid4()}.bin")
        if not file_uuid or not access_token:
            resolved.append(block)
            continue

        api_url = base_url or "https://api.anthropic.com"
        try:
            os.makedirs(upload_dir, exist_ok=True)
            filepath = os.path.join(upload_dir, filename)

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{api_url}/api/oauth/files/{file_uuid}/content",
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT_MS / 1000),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.read()
                        with open(filepath, "wb") as f:
                            f.write(data)
                        resolved.append({
                            "type": "text",
                            "text": f"@{filepath}",
                        })
                    else:
                        logger.debug("[bridge:inbound-attach] Download failed: %d", resp.status)
                        resolved.append(block)
        except Exception as err:
            logger.debug("[bridge:inbound-attach] Error: %s", err)
            resolved.append(block)

    return resolved
