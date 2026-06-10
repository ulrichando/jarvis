"""web_search and web_fetch tools for the JARVIS voice-agent registry.

Ported from the working implementations in jarvis_agent.py (the @function_tool
decorated ``web_search`` and ``web_fetch`` at lines ~2819 and ~2966). Those
implementations are battle-tested in production; this file re-registers them
through the registry framework so they appear via load_all_livekit_tools()
alongside the other self-registering tools.

Backend: DuckDuckGo HTML endpoint (keyless, no rate-limit auth token required).
Falls back to DDG Instant Answer JSON API when the HTML endpoint returns a
CAPTCHA/anomaly page.

Required env var:
  None — uses DuckDuckGo which is keyless. Both tools are always available.

Notes:
  - web_search is async (awaits the blocking urllib call in a thread).
  - web_fetch is async (awaits the blocking urllib call in a thread).
  - Both cap response size to protect token budget.
  - HTML stripping is lightweight / regex-based (no heavy parser deps).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import urllib.error
import urllib.parse as _up
import urllib.request

from .registry import registry
from .url_safety import check_url

logger = logging.getLogger(__name__)

# Cap on returned text after HTML stripping (~3 KB fits voice context well).
_FETCH_CHAR_CAP = 3_072

# Firefox UA — required by DuckDuckGo HTML endpoint (JARVIS-voice UA gets 403).
_BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"


# ---------------------------------------------------------------------------
# DDG Instant Answer fallback (for anomaly/CAPTCHA pages)
# ---------------------------------------------------------------------------

def _ddg_instant_answer(query: str) -> str | None:
    """Query the DDG Instant Answer JSON API (keyless, different rate-limit path).

    Returns a preformatted string on success, None when no useful answer is
    available (so the caller can escalate to transfer_to_browser).

    Useful for: Wikipedia-backed entities, calculator queries, definitions.
    Not useful for: multi-word ranked queries ("kids coding classes pricing"),
    real-time data, niche entities.

    This is synchronous — callers run it via asyncio.to_thread.
    """
    try:
        url = "https://api.duckduckgo.com/?" + _up.urlencode({
            "q": query, "format": "json",
            "no_html": "1", "skip_disambig": "1",
        })
        req = urllib.request.Request(url, headers={"User-Agent": "JARVIS/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read(64 * 1024).decode("utf-8", errors="replace"))
    except Exception as e:
        logger.debug("[ddg-ia] fetch failed: %s: %s", type(e).__name__, e)
        return None

    abstract = (data.get("AbstractText") or data.get("Abstract") or "").strip()
    answer = (data.get("Answer") or "").strip()
    definition = (data.get("Definition") or "").strip()
    heading = (data.get("Heading") or "").strip()
    src = data.get("AbstractSource") or data.get("DefinitionSource") or "DuckDuckGo"
    src_url = data.get("AbstractURL") or data.get("DefinitionURL") or ""

    body = abstract or answer or definition
    if not body:
        topics = data.get("RelatedTopics") or []
        if topics and isinstance(topics[0], dict):
            body = (topics[0].get("Text") or "").strip()
            if body:
                src = "DuckDuckGo (related)"
                src_url = topics[0].get("FirstURL", src_url)

    if not body:
        return None

    parts = []
    if heading:
        parts.append(f"{heading}: {body}")
    else:
        parts.append(body)
    parts.append(f"Source: {src}" + (f" ({src_url})" if src_url else ""))
    parts.append(
        "(Result from DuckDuckGo Instant Answer fallback — the main "
        "search backend is currently rate-limited. For ranked / "
        "multi-source research, consider transfer_to_browser.)"
    )
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# web_search handler
# ---------------------------------------------------------------------------

def _format_provider_results(items: list, n: int) -> str:
    """Format a credentialed provider's search items into the web_search shape.

    Items are ``{"title","url","description",...}`` dicts (the provider contract
    in :mod:`tools.web_providers`). Returns the same numbered-list shape the DDG
    path produces, so callers can't tell which backend served the query.
    """
    out: list = []
    for it in items[:n]:
        if not isinstance(it, dict):
            continue
        title = (it.get("title") or "").strip()
        url_real = (it.get("url") or "").strip()
        snippet = (it.get("description") or "").strip()
        snippet = (snippet[:160] + "…") if len(snippet) > 160 else snippet
        out.append(f"{len(out) + 1}. {title}\n   {url_real}\n   {snippet}")
    return "\n".join(out)


async def _handle_web_search(args: dict) -> str:
    """Async handler for web_search.

    Searches DuckDuckGo HTML endpoint, falls back to DDG Instant Answer
    JSON API on CAPTCHA, returns formatted result list.
    """
    query: str = (args.get("query") or "").strip()
    if not query:
        return "No search query supplied. Ask the user what to search for."
    limit = args.get("limit", 5)
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 5
    n = max(1, min(limit, 10))

    logger.info("web_search → %r (n=%d)", query, n)

    # Prefer a credentialed search backend when one is configured — better
    # ranking and immune to DDG's IP-based CAPTCHA. Falls back to keyless DDG
    # when no provider is available or the provider errors / returns nothing.
    try:
        from tools.web_providers import first_search_provider, run_provider_search

        _prov = first_search_provider()
    except Exception:  # noqa: BLE001 — the provider layer must never break web_search
        _prov = None
    if _prov is not None:
        try:
            _pres = await run_provider_search(_prov, query, n)
            if isinstance(_pres, dict) and _pres.get("success"):
                _items = (_pres.get("data") or {}).get("web") or []
                _formatted = _format_provider_results(_items, n)
                if _formatted:
                    logger.info(
                        "[web_search] served by credentialed backend %r",
                        getattr(_prov, "name", "?"),
                    )
                    return _formatted
        except Exception as e:  # noqa: BLE001 — fall through to DDG on any error
            logger.warning(
                "[web_search] credentialed backend %r failed (%s); using DDG",
                getattr(_prov, "name", "?"),
                e,
            )

    url = "https://html.duckduckgo.com/html/"
    params = _up.urlencode({"q": query})
    full_url = f"{url}?{params}"

    def _fetch_html() -> str:
        req = urllib.request.Request(full_url, headers={
            "User-Agent": _BROWSER_UA,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.5",
        })
        with urllib.request.urlopen(req, timeout=12) as resp:
            return resp.read(256 * 1024).decode("utf-8", errors="replace")

    try:
        html = await asyncio.to_thread(_fetch_html)
    except urllib.error.HTTPError as e:
        return (
            f"Search service unavailable [status={e.code}]. "
            "Tell the user briefly and offer to try again."
        )
    except urllib.error.URLError as e:
        return (
            f"Search service unreachable [{e.reason}]. "
            "Tell the user briefly and offer to try again."
        )
    except Exception as e:
        return (
            f"Search failed [{type(e).__name__}]. "
            "Tell the user briefly and offer to try again."
        )

    # DDG anomaly / CAPTCHA challenge detection.
    if "anomaly-modal" in html or 'data-testid="anomaly' in html:
        logger.warning(
            "[web_search] DDG anomaly/CAPTCHA for %r (html_size=%d); "
            "trying Instant Answer JSON fallback",
            query, len(html),
        )
        ia = await asyncio.to_thread(_ddg_instant_answer, query)
        if ia:
            logger.info("[web_search] Instant Answer fallback returned for %r", query)
            return ia
        return (
            "Search backend (DuckDuckGo) is rate-limiting this IP and "
            "blocked the query with a CAPTCHA. The keyless Instant Answer "
            "fallback also returned nothing for this query. DO NOT retry "
            "with a rephrased query — every variation hits the same block. "
            "Three honest options, in order of preference:\n"
            "  (a) **Escalate to transfer_to_browser** — the browser "
            "      subagent drives the user's real signed-in Chrome via "
            "      the bridge extension, which bypasses server-side rate "
            "      limits. Hand off with transfer_to_browser('search Google for <query>').\n"
            "  (b) Answer from your own knowledge with uncertainty marked "
            "      explicitly ('as of my training data' / 'I'm not sure').\n"
            "  (c) Ask the user for a specific URL and use web_fetch on it.\n"
            "Voice path: 'Search is currently blocked by the backend — "
            "want me to have the browser subagent look it up in your Chrome, "
            "or answer from what I know?'"
        )

    # Parse DDG HTML result anchors and snippets.
    anchor_re = re.compile(
        r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
        re.DOTALL | re.IGNORECASE,
    )
    snippet_re = re.compile(
        r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
        re.DOTALL | re.IGNORECASE,
    )
    anchors = anchor_re.findall(html)
    snippets = snippet_re.findall(html)

    # DDG injects sponsored "ad" units that reuse class="result__a" but whose
    # href is the y.js ad-click tracker (ad_domain/ad_provider/ad_type) instead
    # of the organic /l/?uddg= redirect. Drop them so a paid placement can't
    # surface as organic result #1 and mislead the model. (Live-verify finding
    # 2026-06 — instagram-style ad was being returned as the top result.)
    def _is_ad(href: str) -> bool:
        h = href.lower()
        return "y.js" in h or "ad_domain=" in h or "ad_provider=" in h or "ad_type=" in h

    anchors = [(href, title) for (href, title) in anchors if not _is_ad(href)]

    def _strip_tags(s: str) -> str:
        s = re.sub(r"<[^>]+>", " ", s)
        s = re.sub(r"&amp;", "&", s)
        s = re.sub(r"&quot;", '"', s)
        s = re.sub(r"&#x27;|&apos;", "'", s)
        s = re.sub(r"&lt;", "<", s)
        s = re.sub(r"&gt;", ">", s)
        s = re.sub(r"&nbsp;", " ", s)
        return re.sub(r"\s+", " ", s).strip()

    def _real_url(redirect: str) -> str:
        try:
            parsed = _up.urlparse(redirect)
            qs = _up.parse_qs(parsed.query)
            if "uddg" in qs:
                return _up.unquote(qs["uddg"][0])
        except Exception:
            pass
        return redirect.lstrip("/")

    results = []
    for i, (href, title_html) in enumerate(anchors[:n]):
        title = _strip_tags(title_html)
        url_real = _real_url(href)
        snippet = _strip_tags(snippets[i]) if i < len(snippets) else ""
        snippet = (snippet[:160] + "…") if len(snippet) > 160 else snippet
        results.append(f"{len(results)+1}. {title}\n   {url_real}\n   {snippet}")

    if not results:
        return (
            f"No search results for {query!r}. "
            "Ask the user to rephrase or try a different angle."
        )
    return "\n".join(results)


# ---------------------------------------------------------------------------
# web_fetch handler
# ---------------------------------------------------------------------------

async def _handle_web_fetch(args: dict) -> str:
    """Async handler for web_fetch.

    GETs a URL, strips HTML to plain text, caps at _FETCH_CHAR_CAP chars.
    """
    url: str = (args.get("url") or "").strip()
    if not url:
        return "(no url supplied)"

    # SSRF guard — check the raw URL BEFORE scheme normalization so that
    # non-http(s) schemes (file://, gopher://, etc.) are caught as-is,
    # rather than being masked by the https:// prepend below.
    ssrf_denial = check_url(url)
    if ssrf_denial:
        return ssrf_denial

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    # Re-check after normalization in case a bare hostname resolved to a
    # private IP that wasn't obvious from the raw string (e.g. "localhost").
    ssrf_denial = check_url(url)
    if ssrf_denial:
        return ssrf_denial

    timeout_raw = args.get("timeout", 15)
    try:
        timeout = max(1, min(int(timeout_raw), 60))
    except (TypeError, ValueError):
        timeout = 15

    logger.info("web_fetch → %s", url)

    def _fetch() -> str:
        req = urllib.request.Request(url, headers={"User-Agent": "JARVIS-voice/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ct = resp.headers.get("Content-Type", "")
            raw = resp.read(64 * 1024)
            if "text" not in ct and "json" not in ct and "html" not in ct:
                return f"(non-text content-type: {ct or 'unknown'})"
            return raw.decode("utf-8", errors="replace")

    try:
        body = await asyncio.to_thread(_fetch)
    except urllib.error.HTTPError as e:
        return (
            f"The page could not be retrieved — the site is unavailable [status={e.code}]. "
            "Tell the user briefly and offer to try a different source."
        )
    except urllib.error.URLError as e:
        return (
            f"The page could not be retrieved — network failure [{e.reason}]. "
            "Tell the user briefly and offer to try again."
        )
    except Exception as e:
        return (
            f"The page could not be retrieved — fetch failed [{type(e).__name__}]. "
            "Tell the user briefly and offer to try again."
        )

    # Strip HTML to plain-ish text.
    body = re.sub(r"<script\b.*?</script>", "", body, flags=re.DOTALL | re.IGNORECASE)
    body = re.sub(r"<style\b.*?</style>", "", body, flags=re.DOTALL | re.IGNORECASE)
    body = re.sub(r"<[^>]+>", " ", body)
    body = re.sub(r"\s+", " ", body).strip()

    if len(body) > _FETCH_CHAR_CAP:
        body = body[:_FETCH_CHAR_CAP] + f"… [truncated at {_FETCH_CHAR_CAP} chars]"
    return body


# ---------------------------------------------------------------------------
# Availability check — always True (no API key needed for DDG)
# ---------------------------------------------------------------------------

def _check_web_tools() -> bool:
    """Web tools use DuckDuckGo (keyless). Always available."""
    return True


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

_WEB_SEARCH_SCHEMA = {
    "name": "web_search",
    "description": (
        "Search the web and return the top results (title + URL + snippet). "
        "Use for ANY 'search the web for X' / 'find information on X' / "
        "'what does the internet say about X' — questions where you don't "
        "already know the URL.\n\n"
        "NEVER use web_fetch for search — guessing a URL fails too often. "
        "Use this tool first; THEN web_fetch one of the returned URLs if you "
        "need deeper detail. For multi-step / interactive web work, use "
        "browser_task (drives a real browser via cdp-use).\n\n"
        "DO NOT fabricate search results or claim 'I found X online' UNLESS "
        "this tool has actually been called this turn and returned results.\n\n"
        "Returns up to `limit` (default 5, max 10) entries formatted as:\n"
        "  1. <title>\n"
        "     <url>\n"
        "     <snippet>\n\n"
        "Backend: DuckDuckGo HTML endpoint (keyless). Falls back to DDG Instant "
        "Answer JSON API on rate-limit / CAPTCHA."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query to look up on the web.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of results to return. Defaults to 5, max 10.",
                "minimum": 1,
                "maximum": 10,
                "default": 5,
            },
        },
        "required": ["query"],
    },
}

_WEB_FETCH_SCHEMA = {
    "name": "web_fetch",
    "description": (
        "GET a URL and return its body as plain text (HTML stripped). "
        "Use for atomic 'fetch <url> and tell me what it says' tasks.\n\n"
        "Caps response at ~3 KB after stripping. Times out at `timeout` seconds "
        "(default 15, max 60).\n\n"
        "For structured search-and-summarize across multiple sources, use "
        "web_search + browser_task instead.\n\n"
        "DO NOT summarize what a page says before calling this tool — "
        "claiming content without fetching is confab."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The URL to fetch. https:// is prepended if missing.",
            },
            "timeout": {
                "type": "integer",
                "description": "HTTP timeout in seconds. Defaults to 15, max 60.",
                "default": 15,
                "minimum": 1,
                "maximum": 60,
            },
        },
        "required": ["url"],
    },
}

# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

registry.register(
    name="web_search",
    schema=_WEB_SEARCH_SCHEMA,
    handler=_handle_web_search,
    toolset="web",
    check_fn=_check_web_tools,
    is_async=True,
    emoji="🔍",
    max_result_size_chars=20_000,
)

registry.register(
    name="web_fetch",
    schema=_WEB_FETCH_SCHEMA,
    handler=_handle_web_fetch,
    toolset="web",
    check_fn=_check_web_tools,
    is_async=True,
    emoji="🌐",
    max_result_size_chars=10_000,
)
