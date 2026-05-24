"""Brave Search (free tier) web backend — search-only provider."""
from __future__ import annotations

import logging
import os
from typing import Any, Dict

from tools.web_providers import WebSearchProvider

logger = logging.getLogger(__name__)

_BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"


class BraveFreeWebSearchProvider(WebSearchProvider):
    """Search-only Brave provider using the free-tier Data-for-Search API.

    Free tier is 2,000 queries/month (1 qps). No content-extraction capability —
    pair with Firecrawl/Tavily/Exa for ``web_extract``.
    """

    # Hyphen form preserved for config-key compatibility.
    name = "brave-free"

    def is_available(self) -> bool:
        """Return True when BRAVE_SEARCH_API_KEY is set to a non-empty value."""
        return bool(os.getenv("BRAVE_SEARCH_API_KEY", "").strip())

    def supports_search(self) -> bool:
        return True

    def supports_extract(self) -> bool:
        return False

    def supports_crawl(self) -> bool:
        return False

    def search(self, query: str, limit: int = 5) -> Dict[str, Any]:
        """Execute a search against the Brave Search API.

        Returns ``{"success": True, "data": {"web": [...]}}`` on success,
        ``{"success": False, "error": str}`` on failure.
        """
        import httpx

        api_key = os.getenv("BRAVE_SEARCH_API_KEY", "").strip()
        if not api_key:
            return {"success": False, "error": "BRAVE_SEARCH_API_KEY is not set"}

        # Brave's ``count`` is capped at 20.
        count = max(1, min(int(limit), 20))

        try:
            resp = httpx.get(
                _BRAVE_ENDPOINT,
                params={"q": query, "count": count},
                headers={
                    "X-Subscription-Token": api_key,
                    "Accept": "application/json",
                },
                timeout=15,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning("Brave Search HTTP error: %s", exc)
            return {
                "success": False,
                "error": f"Brave Search returned HTTP {exc.response.status_code}",
            }
        except httpx.RequestError as exc:
            logger.warning("Brave Search request error: %s", exc)
            return {"success": False, "error": f"Could not reach Brave Search: {exc}"}

        try:
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Brave Search response parse error: %s", exc)
            return {
                "success": False,
                "error": "Could not parse Brave Search response as JSON",
            }

        raw_results = (data.get("web") or {}).get("results", []) or []
        truncated = raw_results[:limit]

        web_results = [
            {
                "title": str(r.get("title", "")),
                "url": str(r.get("url", "")),
                "description": str(r.get("description", "")),
                "position": i + 1,
            }
            for i, r in enumerate(truncated)
        ]

        logger.info(
            "Brave Search '%s': %d results (from %d raw, limit %d)",
            query,
            len(web_results),
            len(raw_results),
            limit,
        )

        return {"success": True, "data": {"web": web_results}}


def register(ctx) -> None:
    ctx.register_web_search_provider(BraveFreeWebSearchProvider())
