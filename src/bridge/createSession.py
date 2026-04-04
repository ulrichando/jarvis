"""Create, fetch, archive, and update bridge sessions."""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

import aiohttp

from .debugUtils import extract_error_detail
from .sessionIdCompat import to_compat_session_id

logger = logging.getLogger(__name__)


async def create_bridge_session(
    environment_id: str,
    events: list[dict],
    git_repo_url: Optional[str],
    branch: str,
    title: Optional[str] = None,
    base_url: Optional[str] = None,
    get_access_token: Optional[Callable[[], Optional[str]]] = None,
    permission_mode: Optional[str] = None,
) -> Optional[str]:
    """Create a session on a bridge environment via POST /v1/sessions."""
    access_token = get_access_token() if get_access_token else None
    if not access_token:
        logger.debug("[bridge] No access token for session creation")
        return None

    api_url = base_url or "https://api.anthropic.com"
    url = f"{api_url}/v1/sessions"

    request_body: dict[str, Any] = {
        "events": events,
        "session_context": {"sources": [], "outcomes": []},
        "environment_id": environment_id,
        "source": "remote-control",
    }
    if title is not None:
        request_body["title"] = title
    if permission_mode:
        request_body["permission_mode"] = permission_mode

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "anthropic-beta": "ccr-byoc-2025-07-29",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=request_body, headers=headers) as resp:
                if resp.status not in (200, 201):
                    data = await resp.json()
                    detail = extract_error_detail(data)
                    logger.debug("[bridge] Session creation failed: %s %s", resp.status, detail)
                    return None
                data = await resp.json()
                return data.get("id") if isinstance(data, dict) else None
    except Exception as err:
        logger.debug("[bridge] Session creation request failed: %s", err)
        return None


async def get_bridge_session(
    session_id: str,
    base_url: Optional[str] = None,
    get_access_token: Optional[Callable[[], Optional[str]]] = None,
) -> Optional[dict]:
    """Fetch a bridge session via GET /v1/sessions/{id}."""
    access_token = get_access_token() if get_access_token else None
    if not access_token:
        return None

    api_url = base_url or "https://api.anthropic.com"
    url = f"{api_url}/v1/sessions/{session_id}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "anthropic-beta": "ccr-byoc-2025-07-29",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return None
                return await resp.json()
    except Exception:
        return None


async def archive_bridge_session(
    session_id: str,
    base_url: Optional[str] = None,
    get_access_token: Optional[Callable[[], Optional[str]]] = None,
    timeout_ms: int = 10_000,
) -> None:
    """Archive a bridge session via POST /v1/sessions/{id}/archive."""
    access_token = get_access_token() if get_access_token else None
    if not access_token:
        return

    api_url = base_url or "https://api.anthropic.com"
    url = f"{api_url}/v1/sessions/{session_id}/archive"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "anthropic-beta": "ccr-byoc-2025-07-29",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json={}, headers=headers, timeout=aiohttp.ClientTimeout(total=timeout_ms / 1000)) as resp:
                if resp.status == 200:
                    logger.debug("[bridge] Session %s archived", session_id)
    except Exception as err:
        logger.debug("[bridge] Session archive failed: %s", err)


async def update_bridge_session_title(
    session_id: str,
    title: str,
    base_url: Optional[str] = None,
    get_access_token: Optional[Callable[[], Optional[str]]] = None,
) -> None:
    """Update the title of a bridge session via PATCH /v1/sessions/{id}."""
    access_token = get_access_token() if get_access_token else None
    if not access_token:
        return

    api_url = base_url or "https://api.anthropic.com"
    compat_id = to_compat_session_id(session_id)
    url = f"{api_url}/v1/sessions/{compat_id}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "anthropic-beta": "ccr-byoc-2025-07-29",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.patch(url, json={"title": title}, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    logger.debug("[bridge] Session title updated")
    except Exception as err:
        logger.debug("[bridge] Session title update failed: %s", err)
