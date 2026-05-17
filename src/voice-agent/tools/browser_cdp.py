"""CDP-driven browser tools — the fallback path for the browser
subagent when the Chrome extension isn't connected.

Mirrors the 10 highest-leverage actions from tools/browser_ext.py:
navigate, click, type, key, scroll, get_text, screenshot, list_tabs,
wait_for_load, get_url. Same tool names, same argument shapes,
same return shapes — the supervisor LLM doesn't notice which
backend is active.

Lifecycle: each tool calls `await get_cdp_chrome()` to obtain the
singleton Chromium manager, then `await cdp.get_page()` for the
active page. Chromium is lazily spawned on first call and idle-
shutdown after 5 min (see cdp_chrome.py for details).

The shared `observe` heuristic JS lives in `_browser_observe.js` so
the extension's `_bgObserve` can eventually load the same source.
Until then, the two copies must stay in sync (TODO).

Tested via mocked Playwright Page in tests/test_browser_cdp.py.
"""
from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Optional

from livekit.agents.llm import function_tool

from tools.cdp_chrome import get_cdp_chrome


__all__ = [
    "ext_navigate",
    "ext_click",
    "ext_type",
    "ext_key",
    "ext_scroll",
    "ext_get_text",
    "ext_screenshot",
    "ext_list_tabs",
    "ext_wait_for_load",
    "ext_get_url",
    "ext_observe",
    "CDP_TOOLS",
]


logger = logging.getLogger("jarvis.tools.browser_cdp")


# Load the shared observe JS at import time. Read once, evaluate many.
_OBSERVE_JS = (Path(__file__).parent / "_browser_observe.js").read_text()


# ── Tools ──────────────────────────────────────────────────────────


@function_tool
async def ext_navigate(url: str) -> dict:
    """Navigate the active tab to a URL. Use for "open X" style
    requests. Returns the new URL and the page title once the
    navigation settles.

    Args:
        url: Absolute URL (must include scheme: http:// or https://).
    """
    if not url or not isinstance(url, str):
        return {"ok": False, "error": "url must be a non-empty string"}
    if not url.startswith(("http://", "https://", "chrome://", "about:")):
        return {"ok": False, "error": f"url must include scheme (got {url!r})"}
    cdp = await get_cdp_chrome()
    page = await cdp.get_page()
    try:
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        return {
            "ok": True,
            "url": page.url,
            "title": await page.title(),
            "status": resp.status if resp else None,
        }
    except Exception as e:
        return {"ok": False, "error": f"navigate failed: {type(e).__name__}: {e}"}


@function_tool
async def ext_click(selector: str) -> dict:
    """Click an element matched by CSS selector. Use after `ext_observe`
    has returned a stable selector.

    Args:
        selector: CSS selector (e.g. '#submit', '[aria-label="Search"]').
    """
    if not selector:
        return {"ok": False, "error": "selector required"}
    cdp = await get_cdp_chrome()
    page = await cdp.get_page()
    try:
        await page.click(selector, timeout=10000)
        return {"ok": True, "selector": selector}
    except Exception as e:
        return {"ok": False, "error": f"click failed: {type(e).__name__}: {e}"}


@function_tool
async def ext_type(selector: str, text: str, submit: bool = False) -> dict:
    """Type text into a form field. Clears any existing value first.

    Args:
        selector: CSS selector of the input/textarea.
        text: text to type.
        submit: if True, press Enter after typing (submits most forms).
    """
    if not selector:
        return {"ok": False, "error": "selector required"}
    cdp = await get_cdp_chrome()
    page = await cdp.get_page()
    try:
        await page.fill(selector, text, timeout=10000)
        if submit:
            await page.press(selector, "Enter")
        return {"ok": True, "selector": selector, "submitted": submit}
    except Exception as e:
        return {"ok": False, "error": f"type failed: {type(e).__name__}: {e}"}


@function_tool
async def ext_key(key: str) -> dict:
    """Press a single key (or key combination) globally on the page.
    Use for shortcuts like 'Escape', 'Enter', 'Control+L'.

    Args:
        key: a Playwright key name (e.g. 'Enter', 'Escape', 'PageDown',
             'Control+A'). See https://playwright.dev/python/docs/api/class-keyboard
    """
    if not key:
        return {"ok": False, "error": "key required"}
    cdp = await get_cdp_chrome()
    page = await cdp.get_page()
    try:
        await page.keyboard.press(key)
        return {"ok": True, "key": key}
    except Exception as e:
        return {"ok": False, "error": f"key press failed: {type(e).__name__}: {e}"}


@function_tool
async def ext_scroll(direction: str = "down", amount: int = 500) -> dict:
    """Scroll the active tab.

    Args:
        direction: 'up' | 'down' | 'top' | 'bottom'.
        amount: pixels (ignored for 'top' / 'bottom').
    """
    cdp = await get_cdp_chrome()
    page = await cdp.get_page()
    try:
        if direction == "top":
            await page.evaluate("window.scrollTo(0, 0)")
        elif direction == "bottom":
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        elif direction == "up":
            await page.mouse.wheel(0, -abs(amount))
        elif direction == "down":
            await page.mouse.wheel(0, abs(amount))
        else:
            return {"ok": False, "error": f"unknown direction {direction!r}"}
        return {"ok": True, "direction": direction, "amount": amount}
    except Exception as e:
        return {"ok": False, "error": f"scroll failed: {type(e).__name__}: {e}"}


@function_tool
async def ext_get_text(selector: Optional[str] = None) -> dict:
    """Read text from the page. Without a selector, returns the
    body's visible text (truncated to 5000 chars). With a selector,
    returns that element's text.

    Args:
        selector: optional CSS selector (None = whole page body).
    """
    cdp = await get_cdp_chrome()
    page = await cdp.get_page()
    try:
        if selector:
            text = await page.text_content(selector, timeout=5000)
            return {"ok": True, "selector": selector, "text": text or ""}
        body_text = await page.evaluate("document.body ? document.body.innerText : ''")
        return {
            "ok": True,
            "selector": None,
            "text": (body_text or "")[:5000],
            "truncated": len(body_text or "") > 5000,
        }
    except Exception as e:
        return {"ok": False, "error": f"get_text failed: {type(e).__name__}: {e}"}


@function_tool
async def ext_screenshot(full_page: bool = False) -> dict:
    """Capture a screenshot of the visible viewport (or the full page
    if `full_page=True`). Returns base64-encoded PNG.

    Args:
        full_page: if True, captures the scrolled height; default False
                   (just the viewport).
    """
    cdp = await get_cdp_chrome()
    page = await cdp.get_page()
    try:
        png_bytes = await page.screenshot(full_page=full_page, type="png")
        b64 = base64.b64encode(png_bytes).decode("ascii")
        return {
            "ok": True,
            "image_b64": f"data:image/png;base64,{b64}",
            "bytes": len(png_bytes),
            "full_page": full_page,
        }
    except Exception as e:
        return {"ok": False, "error": f"screenshot failed: {type(e).__name__}: {e}"}


@function_tool
async def ext_list_tabs() -> dict:
    """List all open tabs in the JARVIS Browser context. Returns
    each tab's URL, title, and whether it's the active one.
    """
    cdp = await get_cdp_chrome()
    await cdp.get_page()  # ensure spawned
    try:
        pages = cdp._context.pages  # noqa: SLF001
        active = pages[-1] if pages else None
        out = []
        for i, p in enumerate(pages):
            try:
                out.append({
                    "tab_id": i,
                    "url": p.url,
                    "title": await p.title(),
                    "active": p is active,
                })
            except Exception:
                out.append({"tab_id": i, "url": "(closed)", "title": "", "active": False})
        return {"ok": True, "tabs": out, "count": len(out)}
    except Exception as e:
        return {"ok": False, "error": f"list_tabs failed: {type(e).__name__}: {e}"}


@function_tool
async def ext_wait_for_load(state: str = "load", timeout_ms: int = 10000) -> dict:
    """Wait for the active page to reach a load state.

    Args:
        state: 'load' (full load incl. images) | 'domcontentloaded'
               (DOM parsed) | 'networkidle' (no network for 500ms).
        timeout_ms: cap on the wait (clamped to 1000–60000).
    """
    if state not in ("load", "domcontentloaded", "networkidle"):
        return {"ok": False, "error": "state must be load|domcontentloaded|networkidle"}
    cap = max(1000, min(int(timeout_ms), 60000))
    cdp = await get_cdp_chrome()
    page = await cdp.get_page()
    try:
        await page.wait_for_load_state(state, timeout=cap)
        return {"ok": True, "state": state}
    except Exception as e:
        return {"ok": False, "error": f"wait_for_load failed: {type(e).__name__}: {e}"}


@function_tool
async def ext_get_url() -> dict:
    """Return the active tab's current URL + title."""
    cdp = await get_cdp_chrome()
    page = await cdp.get_page()
    try:
        return {"ok": True, "url": page.url, "title": await page.title()}
    except Exception as e:
        return {"ok": False, "error": f"get_url failed: {type(e).__name__}: {e}"}


@function_tool
async def ext_observe(query: str = "", limit: int = 5) -> dict:
    """Rank interactive elements on the page by relevance to `query`,
    returning ≤`limit` candidates with stable selectors. Heuristic-only;
    no extra LLM call. Use this BEFORE `ext_click` / `ext_type` so the
    supervisor picks a selector deterministically instead of guessing.

    Args:
        query: intent text (e.g. 'sign in', 'search box', 'submit').
               Empty string returns the top-ranked elements by tag weight.
        limit: max candidates to return (capped at 20).
    """
    cdp = await get_cdp_chrome()
    page = await cdp.get_page()
    try:
        # _OBSERVE_JS is an IIFE-style arrow function bound at import.
        # page.evaluate runs the function-expression with (q, lim) args.
        result = await page.evaluate(_OBSERVE_JS, [query, limit])
        return {"ok": True, **result}
    except Exception as e:
        return {"ok": False, "error": f"observe failed: {type(e).__name__}: {e}"}


# Tool registry for the router. Same shape the supervisor expects from
# tools/browser_ext.py — order matches the extension's primary tools so
# the supervisor sees a consistent surface.
CDP_TOOLS = [
    ext_navigate,
    ext_click,
    ext_type,
    ext_key,
    ext_scroll,
    ext_get_text,
    ext_screenshot,
    ext_list_tabs,
    ext_wait_for_load,
    ext_get_url,
    ext_observe,
]
