"""Thin HTTP wrappers for the CCR v2 code-session API."""

from __future__ import annotations

import logging
from typing import Any, Optional

import aiohttp

from .debugUtils import extract_error_detail

logger = logging.getLogger(__name__)

ANTHROPIC_VERSION = "2023-06-01"


def _oauth_headers(access_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "anthropic-version": ANTHROPIC_VERSION,
    }


async def create_code_session(
    base_url: str,
    access_token: str,
    title: str,
    timeout_ms: int = 10_000,
) -> Optional[dict[str, Any]]:
    """Create a code session via POST /v1/code/sessions."""
    url = f"{base_url.rstrip('/')}/v1/code/sessions"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json={"title": title},
                headers=_oauth_headers(access_token),
                timeout=aiohttp.ClientTimeout(total=timeout_ms / 1000),
            ) as resp:
                if resp.status in (200, 201):
                    return await resp.json()
                data = await resp.json()
                detail = extract_error_detail(data)
                logger.debug("[codeSessionApi] create failed %d: %s", resp.status, detail)
                return None
    except Exception as err:
        logger.debug("[codeSessionApi] create error: %s", err)
        return None


async def fetch_remote_credentials(
    base_url: str,
    access_token: str,
    session_id: str,
    timeout_ms: int = 10_000,
) -> Optional[dict[str, Any]]:
    """Fetch bridge credentials via POST /v1/code/sessions/{id}/bridge."""
    url = f"{base_url.rstrip('/')}/v1/code/sessions/{session_id}/bridge"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json={},
                headers=_oauth_headers(access_token),
                timeout=aiohttp.ClientTimeout(total=timeout_ms / 1000),
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                data = await resp.json()
                detail = extract_error_detail(data)
                logger.debug("[codeSessionApi] bridge failed %d: %s", resp.status, detail)
                return None
    except Exception as err:
        logger.debug("[codeSessionApi] bridge error: %s", err)
        return None
