"""Browser-extension @function_tools — navigation + search shortcut.

Hoisted from `tools/browser_ext.py` 2026-05-10 (Step 7 of the audit
— browser_ext regrouping). Each @function_tool here is a thin
wrapper around the shared `_browser_ext_base.post` helper; the
4-way split groups them by responsibility so the LLM-facing API
surface is easier to navigate when adding new tools or debugging
a specific behavior class.
"""
from __future__ import annotations

from typing import Optional
from livekit.agents import function_tool

from tools._browser_ext_base import post, summarize


# Production agents (browser-use, Stagehand) skip the search-box
# DOM entirely for known sites and navigate to the search-results
# URL directly. This sidesteps shadow-DOM problems (YouTube's
# `<input id="search">` lives in a Web Component) AND collapses
# the 5-step chain (navigate → wait → observe → type → submit) into
# ONE tool call. URL templates copied verbatim from browser-use's
# `search` action.
import urllib.parse

_SEARCH_URLS = {
    "youtube":    "https://www.youtube.com/results?search_query={q}",
    "google":     "https://www.google.com/search?q={q}",
    "bing":       "https://www.bing.com/search?q={q}",
    "duckduckgo": "https://duckduckgo.com/?q={q}",
    "amazon":     "https://www.amazon.com/s?k={q}",
    "maps":       "https://www.google.com/maps/search/{q}",
    "wikipedia":  "https://en.wikipedia.org/wiki/Special:Search?search={q}",
}


@function_tool
async def ext_navigate(url: str) -> str:
    """Navigate the active tab to `url`. Use for "go to X.com" /
    "open this URL" requests. Returns the new URL the tab landed on.

    Args:
        url: Full URL including protocol (https://example.com).
    """
    return summarize(await post("navigate", url=url))


@function_tool
async def ext_new_tab(url: Optional[str] = None) -> str:
    """Open a BRAND-NEW tab in Chrome (Ctrl+T equivalent). Does NOT
    close or replace the currently-active tab. Use for "open a new
    tab" / "open a tab" / "new tab" requests. Optionally navigates
    the new tab to `url` if provided; otherwise lands on Chrome's
    new-tab page.

    Args:
        url: Optional URL to load in the new tab. None / omitted →
             Chrome's new-tab page.
    """
    # `url: Optional[str] = None` was originally chosen to make Groq
    # strict-mode tool-call validation accept a call with the property
    # omitted. That alone wasn't sufficient — strict mode strips
    # `default` from the property and forces every property into
    # `required`. The actual fix lives at sanitizers/strict_schema_relax.py
    # (W-009, RFC-001 follow-up): it drops defaulted params from
    # `required` AND drops `additionalProperties:false` so the resulting
    # hybrid-valid schema lets the LLM omit `url`. See POSTMORTEM-001
    # for the regression caught + fixed mid-session.
    return summarize(await post("new_tab", url=url or ""))


@function_tool
async def ext_back() -> str:
    """Go back one step in the active tab's history. Equivalent to
    pressing the browser back button."""
    return summarize(await post("back"))


@function_tool
async def ext_forward() -> str:
    """Go forward one step in the active tab's history. Equivalent to
    pressing the browser forward button."""
    return summarize(await post("forward"))


@function_tool
async def ext_get_url() -> str:
    """Return the URL of the currently active tab. Use for "where am
    I?" or "what page is this?" requests, or as a sanity check
    after navigation."""
    return summarize(await post("get_url"))


@function_tool
async def ext_close_tab() -> str:
    """Close the active tab. Use after the user is done with a task on
    a specific page."""
    return summarize(await post("close_tab"))


@function_tool
async def ext_list_tabs() -> str:
    """List all open browser tabs (mirror of Playwright MCP's
    `browser_tabs`). Returns a compact summary of each tab's id, url,
    title, and which one is active. Use to answer "what's open?" or
    before switching tabs.
    """
    return summarize(await post("list_tabs"))


@function_tool
async def web_search(engine: str, query: str, new_tab: bool = False) -> str:
    """Search a known website by going DIRECTLY to its results URL —
    no DOM clicking, no shadow-DOM searching, no typing required.

    Use this for any "search X for Y" / "find Y on X" voice request
    when X is one of the known engines. Collapses what would be a
    5-step chain (navigate, wait, observe, type, press Enter) into
    ONE tool call.

    Args:
        engine: One of 'youtube', 'google', 'bing', 'duckduckgo',
                'amazon', 'maps', 'wikipedia'. Unknown engines fall
                back to Google.
        query:  The search term, plain text (URL-encoding handled here).
        new_tab: If True, opens results in a brand-new tab. Default
                 False (replaces the current tab) — matches the
                 voice-flow "open YouTube and search" intent.

    Returns:
        One-line confirmation string with engine + query, e.g.
        "Searched YouTube for cooking videos."
    """
    eng = (engine or "").strip().lower()
    template = _SEARCH_URLS.get(eng) or _SEARCH_URLS["google"]
    encoded = urllib.parse.quote(query or "")
    url = template.format(q=encoded)

    if new_tab:
        result = await post("new_tab", url=url)
    else:
        result = await post("navigate", url=url)

    if not result.get("ok"):
        return summarize(result)

    pretty_engine = eng if eng in _SEARCH_URLS else f"Google (engine={eng!r} unknown)"
    return f"Searched {pretty_engine} for {query!r}."


__all__ = ['ext_navigate', 'ext_new_tab', 'ext_back', 'ext_forward', 'ext_get_url', 'ext_close_tab', 'ext_list_tabs', 'web_search']
