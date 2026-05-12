"""Browser-extension @function_tools — navigation + search shortcut.

Hoisted from `tools/browser_ext.py` 2026-05-10 (Step 7 of the audit
— browser_ext regrouping). Each @function_tool here is a thin
wrapper around the shared `_browser_ext_base.post` helper; the
4-way split groups them by responsibility so the LLM-facing API
surface is easier to navigate when adding new tools or debugging
a specific behavior class.

Navigation tools (`ext_navigate`, `ext_new_tab`, `web_search`)
additionally bring Chrome to the foreground via `_focus_chrome_window`
after a successful post. Added 2026-05-12 after a live session where
JARVIS opened Google Maps in a background Chrome window while the
user was looking at VS Code — the navigation succeeded but the user
couldn't see it, so the supervisor's "Maps is open" narrative was
indistinguishable from a hallucination.
"""
from __future__ import annotations

import logging
import subprocess
from typing import Optional
from livekit.agents import function_tool

from tools._browser_ext_base import post, summarize


_logger = logging.getLogger("jarvis.tools.browser_ext_nav")


def _focus_chrome_window() -> None:
    """Best-effort: activate the most-recently-active Chrome window.

    Runs `wmctrl -a "Google Chrome"` — substring-matches the window
    title; every Chrome window ends with " - Google Chrome". Silent
    on failure (wmctrl not installed, no Chrome window open, or any
    bare crash all collapse to a no-op). The nav already landed; the
    tool result must not depend on whether window-focus succeeded.

    NOT called from `ext_back` / `ext_forward` — those run during a
    sequence the user is already watching, and surprise-focusing
    Chrome in the middle of unrelated work would be worse than the
    back-button cost.
    """
    try:
        subprocess.run(
            ["wmctrl", "-a", "Google Chrome"],
            check=False, timeout=2.0,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        _logger.debug(f"[focus-chrome] swallowed: {e}")


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
    result = await post("navigate", url=url)
    if result.get("ok"):
        _focus_chrome_window()
    return summarize(result)


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
    result = await post("new_tab", url=url or "")
    if result.get("ok"):
        _focus_chrome_window()
    return summarize(result)


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

    _focus_chrome_window()
    pretty_engine = eng if eng in _SEARCH_URLS else f"Google (engine={eng!r} unknown)"
    return f"Searched {pretty_engine} for {query!r}."


__all__ = ['ext_navigate', 'ext_new_tab', 'ext_back', 'ext_forward', 'ext_get_url', 'ext_close_tab', 'ext_list_tabs', 'web_search']
