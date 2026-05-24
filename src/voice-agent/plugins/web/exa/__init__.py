"""Exa web backend — credentialed search + extract provider."""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List

from tools.web_providers import WebSearchProvider

logger = logging.getLogger(__name__)

# Module-level client cache — keyed off the process lifetime. Dropped on
# key rotation but never between tool calls.
_exa_client: Any = None


def _get_exa_client() -> Any:
    """Lazy-import and cache an Exa SDK client.

    Raises ``ValueError`` when EXA_API_KEY is unset.
    Raises ``ImportError`` when the exa-py SDK is not installed.
    """
    global _exa_client
    if _exa_client is not None:
        return _exa_client

    api_key = os.getenv("EXA_API_KEY")
    if not api_key:
        raise ValueError(
            "EXA_API_KEY environment variable not set. "
            "Get your API key at https://exa.ai"
        )

    try:
        from exa_py import Exa  # noqa: WPS433 — deliberately lazy
    except ImportError as exc:
        raise ImportError(f"exa-py SDK not installed (pip install exa-py): {exc}") from exc

    client = Exa(api_key=api_key)
    # Identify as JARVIS in the integration header so Exa support can route issues.
    if hasattr(client, "headers"):
        client.headers["x-exa-integration"] = "jarvis-agent"
    _exa_client = client
    return client


class ExaWebSearchProvider(WebSearchProvider):
    """Exa search + extract provider.

    Both methods are sync — Exa's SDK is sync-only. The web_extract tool
    dispatcher wraps sync calls via ``asyncio.to_thread``.
    """

    name = "exa"

    def is_available(self) -> bool:
        """Return True when EXA_API_KEY is set and exa-py is importable."""
        if not os.getenv("EXA_API_KEY", "").strip():
            return False
        try:
            import exa_py  # noqa: F401
        except ImportError:
            return False
        return True

    def supports_search(self) -> bool:
        return True

    def supports_extract(self) -> bool:
        return True

    def supports_crawl(self) -> bool:
        return False

    def search(self, query: str, limit: int = 5) -> Dict[str, Any]:
        """Execute an Exa search.

        Returns ``{"success": True, "data": {"web": [{...}, ...]}}`` on success,
        ``{"success": False, "error": str}`` on failure.
        """
        try:
            logger.info("Exa search: '%s' (limit=%d)", query, limit)
            response = _get_exa_client().search(
                query,
                num_results=limit,
                contents={"highlights": True},
            )

            web_results = []
            for i, result in enumerate(response.results or []):
                highlights = result.highlights or []
                web_results.append(
                    {
                        "url": result.url or "",
                        "title": result.title or "",
                        "description": " ".join(highlights) if highlights else "",
                        "position": i + 1,
                    }
                )

            return {"success": True, "data": {"web": web_results}}
        except ValueError as exc:
            return {"success": False, "error": str(exc)}
        except ImportError as exc:
            return {"success": False, "error": f"Exa SDK not installed: {exc}"}
        except Exception as exc:  # noqa: BLE001
            logger.warning("Exa search error: %s", exc)
            return {"success": False, "error": f"Exa search failed: {exc}"}

    def extract(self, urls: List[str], **kwargs: Any) -> Dict[str, Any]:
        """Extract content from one or more URLs via Exa.

        Returns the standard extract shape:
        ``{"success": True, "data": [{url, title, content, raw_content, metadata}]}``.
        """
        try:
            logger.info("Exa extract: %d URL(s)", len(urls))
            response = _get_exa_client().get_contents(urls, text=True)

            results: List[Dict[str, Any]] = []
            for result in response.results or []:
                content = result.text or ""
                url = result.url or ""
                title = result.title or ""
                results.append(
                    {
                        "url": url,
                        "title": title,
                        "content": content,
                        "raw_content": content,
                        "metadata": {"sourceURL": url, "title": title},
                    }
                )
            return {"success": True, "data": results}
        except ValueError as exc:
            return {"success": False, "error": str(exc)}
        except ImportError as exc:
            return {"success": False, "error": f"Exa SDK not installed: {exc}"}
        except Exception as exc:  # noqa: BLE001
            logger.warning("Exa extract error: %s", exc)
            return {"success": False, "error": f"Exa extract failed: {exc}"}


def register(ctx) -> None:
    ctx.register_web_search_provider(ExaWebSearchProvider())
