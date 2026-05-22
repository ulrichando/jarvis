"""JARVIS-native web provider base + ``web_extract`` / ``web_crawl`` tools.

The keyless ``web_search`` / ``web_fetch`` tools in :mod:`tools.web_tools`
(DuckDuckGo, always available) are unchanged. This module adds the *credentialed*
side that keyless DDG can't do:

* a pluggable provider surface under the generic registry kind ``"web"``
  (:mod:`tools._provider_registry`), and
* two new consumer tools — ``web_extract`` (clean markdown content from known
  URLs) and ``web_crawl`` (deep site crawl) — that resolve the first available
  provider advertising that capability.

Backend providers live as plugins under ``plugins/web/<name>/`` and self-register
via ``ctx.register_web_search_provider(...)`` (wired into this kind by
:class:`tools.plugin_system.PluginContext`). Each backend gates itself on its own
API key through ``is_available()``; with no key set, both extra tools are filtered
out of the supervisor surface entirely (inert), so JARVIS never offers extract /
crawl it cannot perform.

Ported from the upstream web-search provider ABC + the web_extract / web_crawl
dispatchers, stripped of the ``agent.*`` / ``config.yaml`` / lazy-deps coupling.
No upstream brand tokens.

Provider response contract (preserved so the tool wrappers stay thin):

* ``search(query, limit)`` →
  ``{"success": True, "data": {"web": [{"title","url","description","position"}]}}``
* ``extract(urls)`` →
  ``{"success": True, "data": [{"url","title","content","raw_content","metadata"}]}``
* ``crawl(url, **kw)`` → same item shape as ``extract``.
* failure (any capability) → ``{"success": False, "error": str}``
"""
from __future__ import annotations

import abc
import asyncio
import logging
from typing import Any, Dict, List, Optional

from . import _provider_registry as provider_registry
from .registry import registry, tool_error

logger = logging.getLogger(__name__)

# The provider-registry kind these tools (and the plugins/web/* backends) use.
PROVIDER_KIND = "web"

# Per-item content cap (~6 KB) so a multi-URL extract stays within voice budget.
_EXTRACT_ITEM_CAP = 6_000


# ---------------------------------------------------------------------------
# Provider base
# ---------------------------------------------------------------------------


class WebSearchProvider(abc.ABC):
    """Base class for a credentialed web search / extract / crawl backend.

    Duck-type-compatible with :mod:`tools._provider_registry`: every subclass
    exposes a stable lowercase ``name`` and an ``is_available()`` gate. The three
    ``supports_*`` capability flags default to False; a backend overrides the ones
    it implements so the ``web_extract`` / ``web_crawl`` dispatchers can resolve
    the right provider (a single multi-capability backend may advertise several).
    """

    name: str = ""

    @abc.abstractmethod
    def is_available(self) -> bool:
        """True when this backend can service calls (API key set + SDK importable)."""

    def supports_search(self) -> bool:
        return False

    def supports_extract(self) -> bool:
        return False

    def supports_crawl(self) -> bool:
        return False

    def search(self, query: str, limit: int = 5) -> Dict[str, Any]:
        return {"success": False, "error": f"{self.name or 'provider'} does not support search"}

    def extract(self, urls: List[str]) -> Dict[str, Any]:
        return {"success": False, "error": f"{self.name or 'provider'} does not support extract"}

    def crawl(self, url: str, **kwargs: Any) -> Dict[str, Any]:
        return {"success": False, "error": f"{self.name or 'provider'} does not support crawl"}


# ---------------------------------------------------------------------------
# Capability resolution
# ---------------------------------------------------------------------------


def _first_capable(capability: str) -> Optional[Any]:
    """Return the first *available* web provider advertising ``supports_<capability>``.

    Name-sorted for determinism (via the registry). Exceptions in a provider's
    ``supports_*`` probe are swallowed so one bad backend can't break resolution.
    """
    for provider in provider_registry.available_providers(PROVIDER_KIND):
        probe = getattr(provider, f"supports_{capability}", None)
        try:
            if probe is not None and probe():
                return provider
        except Exception:  # noqa: BLE001 — a capability probe must not raise out
            logger.debug("web provider %r supports_%s() raised", getattr(provider, "name", "?"), capability)
    return None


def check_web_extract_available() -> bool:
    """``check_fn`` for ``web_extract`` — gated on an extract-capable backend."""
    return _first_capable("extract") is not None


def check_web_crawl_available() -> bool:
    """``check_fn`` for ``web_crawl`` — gated on a crawl-capable backend."""
    return _first_capable("crawl") is not None


# ---------------------------------------------------------------------------
# Result formatting
# ---------------------------------------------------------------------------


def _fmt_extract(data: List[Dict[str, Any]], cap: int = _EXTRACT_ITEM_CAP) -> str:
    """Render extract/crawl result items as readable, capped markdown sections."""
    out: List[str] = []
    for item in data or []:
        if not isinstance(item, dict):
            continue
        body = (item.get("content") or item.get("raw_content") or "").strip()
        if len(body) > cap:
            body = body[:cap] + "… [truncated]"
        title = item.get("title") or item.get("url") or "(untitled)"
        url = item.get("url") or ""
        out.append(f"# {title}\n{url}\n\n{body}".rstrip())
    return "\n\n---\n\n".join(out) if out else "(no content extracted)"


# ---------------------------------------------------------------------------
# web_extract handler + tool
# ---------------------------------------------------------------------------


async def _handle_web_extract(args: dict) -> str:
    raw = args.get("urls") if isinstance(args, dict) else None
    if raw is None and isinstance(args, dict):
        raw = args.get("url")
    urls = [raw] if isinstance(raw, str) else list(raw or [])
    urls = [u.strip() for u in urls if isinstance(u, str) and u.strip()]
    if not urls:
        return tool_error("web_extract requires a 'urls' list (or a single 'url').")

    provider = _first_capable("extract")
    if provider is None:
        return tool_error(
            "No extract-capable web backend is available. Set TAVILY_API_KEY, "
            "EXA_API_KEY, FIRECRAWL_API_KEY, or PARALLEL_API_KEY in the "
            "voice-agent environment to enable web_extract.",
            error_type="auth_required",
        )

    logger.info("web_extract → %d url(s) via %s", len(urls), getattr(provider, "name", "?"))
    try:
        result = await asyncio.to_thread(provider.extract, urls)
    except Exception as exc:  # noqa: BLE001 — a provider error must not crash the turn
        logger.warning("web_extract provider %r raised: %s", getattr(provider, "name", "?"), exc)
        return tool_error(f"web_extract failed: {exc}")

    if not isinstance(result, dict) or not result.get("success"):
        return tool_error(
            (result or {}).get("error", "web_extract failed"),
            provider=getattr(provider, "name", ""),
        )
    return _fmt_extract(result.get("data") or [])


async def _handle_web_crawl(args: dict) -> str:
    url = (args.get("url") or "").strip() if isinstance(args, dict) else ""
    if not url:
        return tool_error("web_crawl requires a 'url' to seed the crawl.")

    provider = _first_capable("crawl")
    if provider is None:
        return tool_error(
            "No crawl-capable web backend is available. Set TAVILY_API_KEY or "
            "FIRECRAWL_API_KEY in the voice-agent environment to enable web_crawl.",
            error_type="auth_required",
        )

    logger.info("web_crawl → %s via %s", url, getattr(provider, "name", "?"))
    try:
        result = await asyncio.to_thread(provider.crawl, url)
    except Exception as exc:  # noqa: BLE001
        logger.warning("web_crawl provider %r raised: %s", getattr(provider, "name", "?"), exc)
        return tool_error(f"web_crawl failed: {exc}")

    if not isinstance(result, dict) or not result.get("success"):
        return tool_error(
            (result or {}).get("error", "web_crawl failed"),
            provider=getattr(provider, "name", ""),
        )
    return _fmt_extract(result.get("data") or [])


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

_WEB_EXTRACT_SCHEMA = {
    "name": "web_extract",
    "description": (
        "Extract clean, readable content (markdown) from one or more known URLs "
        "using a credentialed backend (Tavily / Exa / Firecrawl / Parallel). Use "
        "when you need the FULL content of pages you already have URLs for — not a "
        "search. For a quick single-page text grab with no API key, use web_fetch "
        "instead; to find URLs first, use web_search."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "urls": {
                "type": "array",
                "items": {"type": "string"},
                "description": "One or more URLs to extract readable content from.",
            },
        },
        "required": ["urls"],
    },
}

_WEB_CRAWL_SCHEMA = {
    "name": "web_crawl",
    "description": (
        "Deep-crawl a website from a seed URL and return aggregated readable "
        "content. Credentialed backend (Tavily / Firecrawl). Use for "
        "'read everything under <site>' research; for a single page use "
        "web_extract or web_fetch."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Seed URL to crawl from.",
            },
        },
        "required": ["url"],
    },
}


registry.register(
    name="web_extract",
    schema=_WEB_EXTRACT_SCHEMA,
    handler=_handle_web_extract,
    toolset="web",
    check_fn=check_web_extract_available,
    requires_env=["TAVILY_API_KEY", "EXA_API_KEY", "FIRECRAWL_API_KEY", "PARALLEL_API_KEY"],
    is_async=True,
    emoji="📄",
    max_result_size_chars=20_000,
)

registry.register(
    name="web_crawl",
    schema=_WEB_CRAWL_SCHEMA,
    handler=_handle_web_crawl,
    toolset="web",
    check_fn=check_web_crawl_available,
    requires_env=["TAVILY_API_KEY", "FIRECRAWL_API_KEY"],
    is_async=True,
    emoji="🕸️",
    max_result_size_chars=20_000,
)
