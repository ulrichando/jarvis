"""Session history pagination for fetching events from the API."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

import aiohttp

logger = logging.getLogger(__name__)

HISTORY_PAGE_SIZE = 100


@dataclass
class HistoryPage:
    """A page of session history events."""
    events: list[dict[str, Any]] = field(default_factory=list)
    first_id: Optional[str] = None
    has_more: bool = False


@dataclass
class HistoryAuthCtx:
    """Auth context for history API calls."""
    base_url: str
    headers: dict[str, str]


async def create_history_auth_ctx(session_id: str) -> HistoryAuthCtx:
    """Prepare auth + headers + base URL once, reuse across pages."""
    from ..utils.teleport.api import prepare_api_request, get_oauth_headers
    from ..constants.oauth import get_oauth_config

    access_token, org_uuid = await prepare_api_request()
    config = get_oauth_config()
    return HistoryAuthCtx(
        base_url=f"{config.BASE_API_URL}/v1/sessions/{session_id}/events",
        headers={
            **get_oauth_headers(access_token),
            "anthropic-beta": "ccr-byoc-2025-07-29",
            "x-organization-uuid": org_uuid,
        },
    )


async def _fetch_page(
    ctx: HistoryAuthCtx,
    params: dict[str, Any],
    label: str,
) -> Optional[HistoryPage]:
    """Fetch a single page of history events."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                ctx.base_url,
                headers=ctx.headers,
                params=params,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    logger.debug("[%s] HTTP %d", label, resp.status)
                    return None
                data = await resp.json()
                return HistoryPage(
                    events=data.get("data", []) if isinstance(data.get("data"), list) else [],
                    first_id=data.get("first_id"),
                    has_more=data.get("has_more", False),
                )
    except Exception:
        logger.debug("[%s] HTTP error", label)
        return None


async def fetch_latest_events(
    ctx: HistoryAuthCtx,
    limit: int = HISTORY_PAGE_SIZE,
) -> Optional[HistoryPage]:
    """Newest page: last `limit` events, chronological, via anchor_to_latest."""
    return await _fetch_page(
        ctx,
        {"limit": limit, "anchor_to_latest": True},
        "fetchLatestEvents",
    )


async def fetch_older_events(
    ctx: HistoryAuthCtx,
    before_id: str,
    limit: int = HISTORY_PAGE_SIZE,
) -> Optional[HistoryPage]:
    """Older page: events immediately before `before_id` cursor."""
    return await _fetch_page(
        ctx,
        {"limit": limit, "before_id": before_id},
        "fetchOlderEvents",
    )
