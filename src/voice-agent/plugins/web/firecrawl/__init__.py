"""Firecrawl web backend — credentialed search + extract + crawl provider."""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, List, Optional

from tools.web_providers import WebSearchProvider

logger = logging.getLogger(__name__)

# Module-level client cache slot.
_firecrawl_client: Any = None
_firecrawl_client_config: Any = None


def _is_available() -> bool:
    """Return True when FIRECRAWL_API_KEY or FIRECRAWL_API_URL is set."""
    return bool(
        os.getenv("FIRECRAWL_API_KEY", "").strip()
        or os.getenv("FIRECRAWL_API_URL", "").strip()
    )


def _get_firecrawl_client() -> Any:
    """Get or create the cached Firecrawl client.

    Raises ``ValueError`` when neither FIRECRAWL_API_KEY nor FIRECRAWL_API_URL
    is configured. Raises ``ImportError`` when the firecrawl SDK is missing.
    """
    global _firecrawl_client, _firecrawl_client_config

    api_key = os.getenv("FIRECRAWL_API_KEY", "").strip()
    api_url = os.getenv("FIRECRAWL_API_URL", "").strip().rstrip("/")

    if not api_key and not api_url:
        raise ValueError(
            "Firecrawl is not configured. "
            "Set FIRECRAWL_API_KEY for cloud Firecrawl or FIRECRAWL_API_URL "
            "for a self-hosted instance."
        )

    client_config = (api_key or None, api_url or None)
    if _firecrawl_client is not None and _firecrawl_client_config == client_config:
        return _firecrawl_client

    try:
        from firecrawl import Firecrawl as _FirecrawlCls  # noqa: WPS433 — lazy
    except ImportError as exc:
        raise ImportError(
            f"firecrawl SDK not installed (pip install firecrawl-py): {exc}"
        ) from exc

    kwargs: Dict[str, str] = {}
    if api_key:
        kwargs["api_key"] = api_key
    if api_url:
        kwargs["api_url"] = api_url

    _firecrawl_client = _FirecrawlCls(**kwargs)
    _firecrawl_client_config = client_config
    return _firecrawl_client


# ---------------------------------------------------------------------------
# Response normalizers (handle SDK / direct / dict shapes uniformly)
# ---------------------------------------------------------------------------


def _to_plain(value: Any) -> Any:
    """Convert SDK model objects to plain Python data structures."""
    if value is None or isinstance(value, (dict, list, str, int, float, bool)):
        return value
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump()
        except Exception:  # noqa: BLE001
            pass
    if hasattr(value, "__dict__"):
        try:
            return {k: v for k, v in value.__dict__.items() if not k.startswith("_")}
        except Exception:  # noqa: BLE001
            pass
    return value


def _normalize_result_list(values: Any) -> List[Dict[str, Any]]:
    if not isinstance(values, list):
        return []
    result: List[Dict[str, Any]] = []
    for item in values:
        plain = _to_plain(item)
        if isinstance(plain, dict):
            result.append(plain)
    return result


def _extract_search_results(response: Any) -> List[Dict[str, Any]]:
    """Extract Firecrawl search results across SDK/direct/dict response shapes."""
    plain = _to_plain(response)
    if isinstance(plain, dict):
        data = plain.get("data")
        if isinstance(data, list):
            return _normalize_result_list(data)
        if isinstance(data, dict):
            web = _normalize_result_list(data.get("web"))
            if web:
                return web
            results = _normalize_result_list(data.get("results"))
            if results:
                return results
        top_web = _normalize_result_list(plain.get("web"))
        if top_web:
            return top_web
        top_results = _normalize_result_list(plain.get("results"))
        if top_results:
            return top_results
    if hasattr(response, "web"):
        return _normalize_result_list(getattr(response, "web", []))
    return []


def _extract_scrape_payload(scrape_result: Any) -> Dict[str, Any]:
    plain = _to_plain(scrape_result)
    if not isinstance(plain, dict):
        return {}
    nested = plain.get("data")
    if isinstance(nested, dict):
        return nested
    return plain


def _ensure_metadata_dict(metadata_obj: Any) -> Dict[str, Any]:
    if isinstance(metadata_obj, dict):
        return metadata_obj
    if hasattr(metadata_obj, "model_dump"):
        return metadata_obj.model_dump()
    if hasattr(metadata_obj, "__dict__"):
        return {k: v for k, v in metadata_obj.__dict__.items() if not k.startswith("_")}
    return {}


class FirecrawlWebSearchProvider(WebSearchProvider):
    """Firecrawl search + extract + crawl provider (direct API)."""

    name = "firecrawl"

    def is_available(self) -> bool:
        """Return True when FIRECRAWL_API_KEY or FIRECRAWL_API_URL is set and SDK is importable."""
        if not _is_available():
            return False
        try:
            import firecrawl  # noqa: F401
        except ImportError:
            return False
        return True

    def supports_search(self) -> bool:
        return True

    def supports_extract(self) -> bool:
        return True

    def supports_crawl(self) -> bool:
        return True

    def search(self, query: str, limit: int = 5) -> Dict[str, Any]:
        """Execute a Firecrawl search."""
        logger.info("Firecrawl search: '%s' (limit=%d)", query, limit)
        try:
            client = _get_firecrawl_client()
            response = client.search(query=query, limit=limit)
            web_results = _extract_search_results(response)
            logger.info("Firecrawl: found %d search results", len(web_results))
            return {"success": True, "data": {"web": web_results}}
        except (ValueError, ImportError) as exc:
            return {"success": False, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001
            logger.warning("Firecrawl search error: %s", exc)
            return {"success": False, "error": f"Firecrawl search failed: {exc}"}

    def extract(self, urls: List[str], **kwargs: Any) -> Dict[str, Any]:
        """Extract content from one or more URLs via Firecrawl scrape.

        Runs each scrape synchronously (call from asyncio.to_thread at the
        tool layer). Returns the standard extract shape:
        ``{"success": True, "data": [{url, title, content, raw_content, metadata}]}``.
        """
        try:
            client = _get_firecrawl_client()
        except (ValueError, ImportError) as exc:
            return {"success": False, "error": str(exc)}

        fmt = kwargs.get("format")
        if fmt == "markdown":
            formats: List[str] = ["markdown"]
        elif fmt == "html":
            formats = ["html"]
        else:
            formats = ["markdown", "html"]

        results: List[Dict[str, Any]] = []
        for url in urls:
            try:
                logger.info("Firecrawl scraping: %s", url)
                scrape_result = client.scrape(url=url, formats=formats)
                scrape_payload = _extract_scrape_payload(scrape_result)
                metadata = _ensure_metadata_dict(scrape_payload.get("metadata", {}))
                content_markdown = scrape_payload.get("markdown")
                content_html = scrape_payload.get("html")
                title = metadata.get("title", "")
                final_url = metadata.get("sourceURL", url)
                chosen = content_markdown or content_html or ""
                results.append(
                    {
                        "url": final_url,
                        "title": title,
                        "content": chosen,
                        "raw_content": chosen,
                        "metadata": metadata,
                    }
                )
            except Exception as scrape_err:  # noqa: BLE001
                logger.debug("Firecrawl scrape failed for %s: %s", url, scrape_err)
                results.append(
                    {
                        "url": url,
                        "title": "",
                        "content": "",
                        "raw_content": "",
                        "error": str(scrape_err),
                    }
                )

        return {"success": True, "data": results}

    def crawl(self, url: str, **kwargs: Any) -> Dict[str, Any]:
        """Crawl a seed URL via Firecrawl's /crawl endpoint.

        Accepted kwargs (others ignored for forward compat):
          - ``limit``: int — max pages to crawl (default 20)
          - ``instructions``: str — ignored (not supported by Firecrawl /crawl)
          - ``depth``: str — ignored (not supported by Firecrawl /crawl)

        Returns the standard crawl shape:
        ``{"success": True, "data": [{url, title, content, raw_content, metadata}]}``.
        """
        try:
            client = _get_firecrawl_client()
        except (ValueError, ImportError) as exc:
            return {"success": False, "error": str(exc)}

        instructions = kwargs.get("instructions")
        limit = kwargs.get("limit", 20)
        if instructions:
            logger.info(
                "Firecrawl crawl: 'instructions' parameter ignored "
                "(not supported by Firecrawl /crawl)"
            )

        logger.info("Firecrawl crawl: %s (limit=%d)", url, limit)
        try:
            crawl_result = client.crawl(
                url=url,
                limit=limit,
                scrape_options={"formats": ["markdown"]},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Firecrawl crawl error: %s", exc)
            return {"success": False, "error": f"Firecrawl crawl failed: {exc}"}

        # Normalize CrawlJob / dict shapes.
        data_list: List[Any] = []
        if hasattr(crawl_result, "data"):
            data_list = crawl_result.data or []
            logger.info(
                "Firecrawl crawl status: %s, %d pages",
                getattr(crawl_result, "status", "unknown"),
                len(data_list),
            )
        elif isinstance(crawl_result, dict):
            data_list = crawl_result.get("data", []) or []

        pages: List[Dict[str, Any]] = []
        for item in data_list:
            if hasattr(item, "model_dump"):
                item_dict = item.model_dump()
                content_markdown = item_dict.get("markdown")
                content_html = item_dict.get("html")
                metadata = _ensure_metadata_dict(item_dict.get("metadata", {}))
            elif hasattr(item, "__dict__"):
                content_markdown = getattr(item, "markdown", None)
                content_html = getattr(item, "html", None)
                metadata = _ensure_metadata_dict(getattr(item, "metadata", {}))
            elif isinstance(item, dict):
                content_markdown = item.get("markdown")
                content_html = item.get("html")
                metadata = _ensure_metadata_dict(item.get("metadata", {}))
            else:
                continue

            page_url = metadata.get("sourceURL", metadata.get("url", url))
            title = metadata.get("title", "")
            content = content_markdown or content_html or ""
            pages.append(
                {
                    "url": page_url,
                    "title": title,
                    "content": content,
                    "raw_content": content,
                    "metadata": metadata,
                }
            )

        return {"success": True, "data": pages}


def register(ctx) -> None:
    ctx.register_web_search_provider(FirecrawlWebSearchProvider())
