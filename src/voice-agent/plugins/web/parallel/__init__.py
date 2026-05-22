"""Parallel.ai web backend — credentialed search + async extract provider."""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List

from tools.web_providers import WebSearchProvider

logger = logging.getLogger(__name__)

# Module-level client cache slots (sync + async).
_parallel_client: Any = None
_async_parallel_client: Any = None


def _resolve_search_mode() -> str:
    """Return the validated PARALLEL_SEARCH_MODE value (default 'agentic')."""
    mode = os.getenv("PARALLEL_SEARCH_MODE", "agentic").lower().strip()
    if mode not in {"fast", "one-shot", "agentic"}:
        mode = "agentic"
    return mode


def _get_sync_client() -> Any:
    """Lazy-load + cache the sync Parallel client.

    Raises ``ValueError`` when PARALLEL_API_KEY is unset.
    Raises ``ImportError`` when the parallel SDK is missing.
    """
    global _parallel_client

    if _parallel_client is not None:
        return _parallel_client

    api_key = os.getenv("PARALLEL_API_KEY")
    if not api_key:
        raise ValueError(
            "PARALLEL_API_KEY environment variable not set. "
            "Get your API key at https://parallel.ai"
        )

    try:
        from parallel import Parallel  # noqa: WPS433 — deliberately lazy
    except ImportError as exc:
        raise ImportError(
            f"parallel SDK not installed (pip install parallelai): {exc}"
        ) from exc

    _parallel_client = Parallel(api_key=api_key)
    return _parallel_client


def _get_async_client() -> Any:
    """Lazy-load + cache the async Parallel client.

    Raises ``ValueError`` when PARALLEL_API_KEY is unset.
    Raises ``ImportError`` when the parallel SDK is missing.
    """
    global _async_parallel_client

    if _async_parallel_client is not None:
        return _async_parallel_client

    api_key = os.getenv("PARALLEL_API_KEY")
    if not api_key:
        raise ValueError(
            "PARALLEL_API_KEY environment variable not set. "
            "Get your API key at https://parallel.ai"
        )

    try:
        from parallel import AsyncParallel  # noqa: WPS433 — deliberately lazy
    except ImportError as exc:
        raise ImportError(
            f"parallel SDK not installed (pip install parallelai): {exc}"
        ) from exc

    _async_parallel_client = AsyncParallel(api_key=api_key)
    return _async_parallel_client


class ParallelWebSearchProvider(WebSearchProvider):
    """Parallel.ai search + async extract provider."""

    name = "parallel"

    def is_available(self) -> bool:
        """Return True when PARALLEL_API_KEY is set and the SDK is importable."""
        if not os.getenv("PARALLEL_API_KEY", "").strip():
            return False
        try:
            import parallel  # noqa: F401
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
        """Execute a Parallel search (sync).

        Uses the ``beta.search`` endpoint with the configured mode
        (``PARALLEL_SEARCH_MODE`` env var, default "agentic"). Limit is
        capped at 20 server-side.
        """
        try:
            mode = _resolve_search_mode()
            logger.info(
                "Parallel search: '%s' (mode=%s, limit=%d)", query, mode, limit
            )
            response = _get_sync_client().beta.search(
                search_queries=[query],
                objective=query,
                mode=mode,
                max_results=min(limit, 20),
            )

            web_results = []
            for i, result in enumerate(response.results or []):
                excerpts = result.excerpts or []
                web_results.append(
                    {
                        "url": result.url or "",
                        "title": result.title or "",
                        "description": " ".join(excerpts) if excerpts else "",
                        "position": i + 1,
                    }
                )

            return {"success": True, "data": {"web": web_results}}
        except ValueError as exc:
            return {"success": False, "error": str(exc)}
        except ImportError as exc:
            return {"success": False, "error": f"Parallel SDK not installed: {exc}"}
        except Exception as exc:  # noqa: BLE001
            logger.warning("Parallel search error: %s", exc)
            return {"success": False, "error": f"Parallel search failed: {exc}"}

    async def extract(self, urls: List[str], **kwargs: Any) -> Dict[str, Any]:
        """Extract content from one or more URLs via the async Parallel SDK.

        Declared async because the SDK's ``beta.extract`` is async-native.
        The tool dispatcher detects coroutines and awaits directly.

        Returns the standard extract shape:
        ``{"success": True, "data": [{url, title, content, raw_content, metadata}]}``.
        """
        try:
            logger.info("Parallel extract: %d URL(s)", len(urls))
            response = await _get_async_client().beta.extract(
                urls=urls,
                full_content=True,
            )

            results: List[Dict[str, Any]] = []
            for result in response.results or []:
                content = result.full_content or ""
                if not content:
                    content = "\n\n".join(result.excerpts or [])
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

            for error in response.errors or []:
                results.append(
                    {
                        "url": error.url or "",
                        "title": "",
                        "content": "",
                        "error": error.content or error.error_type or "extraction failed",
                        "metadata": {"sourceURL": error.url or ""},
                    }
                )

            return {"success": True, "data": results}
        except ValueError as exc:
            return {"success": False, "error": str(exc)}
        except ImportError as exc:
            return {"success": False, "error": f"Parallel SDK not installed: {exc}"}
        except Exception as exc:  # noqa: BLE001
            logger.warning("Parallel extract error: %s", exc)
            return {"success": False, "error": f"Parallel extract failed: {exc}"}


def register(ctx) -> None:
    ctx.register_web_search_provider(ParallelWebSearchProvider())
