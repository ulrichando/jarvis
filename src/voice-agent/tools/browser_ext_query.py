"""Browser-extension @function_tools — page query + observation.

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


@function_tool
async def ext_extract_text(selector: Optional[str] = None) -> str:
    """Return the visible text of the page (or of `selector` if given).
    Use to get the user's email body, an article, or whatever they're
    looking at. Output is trimmed to ~800 chars; for longer content,
    extract a specific selector instead.

    Args:
        selector: Optional CSS selector to scope the extraction. If
                  omitted, returns the whole page's visible text.
    """
    return summarize(await post("extract_text", selector=selector or ""))


@function_tool
async def ext_find_by_text(text: str) -> str:
    """Locate an element on the page by its visible text content.
    Returns a description of the element (tag, position, classes) so
    the LLM can decide whether to click it. Useful when there's no
    obvious selector — "the Sign In button" / "the link that says
    'Cancel order'".

    Args:
        text: Substring of the visible text to search for. Match is
              case-insensitive and trimmed.
    """
    return summarize(await post("find_by_text", text=text))


@function_tool
async def ext_dom_summary() -> str:
    """High-level summary of the active tab's DOM — list of forms,
    primary buttons, headings, and visible inputs. Use when the user
    asks "what can I do here?" or you need to plan a multi-step
    interaction. Output is trimmed to keep the LLM context small."""
    return summarize(await post("dom_summary"))


@function_tool
async def ext_screenshot() -> str:
    """Capture a screenshot of the active tab. Returns a description
    if the bridge/LLM does vision; otherwise returns a confirmation.
    Use when the page is heavily JS-rendered and DOM-only inspection
    isn't enough."""
    return summarize(await post("screenshot"))


@function_tool
async def ext_get_console(level: Optional[str] = None, limit: int = 25) -> str:
    """Read recent console log entries from the active tab (mirror of
    BrowserMCP's `browser_get_console_logs`). The first call attaches
    chrome.debugger to the tab; subsequent calls reuse the buffer.

    The buffer captures logs ONLY AFTER first attach — reload the
    page if you need startup logs.

    Args:
        level: Filter by 'log', 'warn', 'error', 'info', or 'debug'.
               None / omitted = all levels.
        limit: Most recent N entries (1–100, default 25).
    """
    return summarize(await post("get_console", level=level or "", limit=limit))


@function_tool
async def ext_observe(query: Optional[str] = None, limit: int = 5) -> str:
    """Return a ranked list of actionable elements matching `query`
    (mirror of Stagehand's `observe()` + browser-use's `find_elements`).
    Each match includes a stable selector + suggested action method
    (click/type/select). Use to find the right element when text-based
    lookup is fuzzy.

    Args:
        query: Natural-language description of the element you want.
               Empty = top-N most-actionable elements on the page.
        limit: Max matches to return (1-20, default 5).

    Returns:
        Each match: {selector, tag, role, text, suggested_method, score}.
        Pass the selector + suggested_method to ext_click / ext_type / etc.
    """
    return summarize(await post("observe", query=query or "", limit=limit))


__all__ = ['ext_extract_text', 'ext_find_by_text', 'ext_dom_summary', 'ext_screenshot', 'ext_get_console', 'ext_observe']
