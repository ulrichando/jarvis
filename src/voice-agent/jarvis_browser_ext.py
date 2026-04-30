"""Browser-extension @function_tools — 25 thin wrappers around the
`/api/ext_browse` bridge endpoint.

The bridge (Bun, port 8765 by default) forwards each command to the
connected jarvis-screen Chrome extension over WebSocket and waits for
the response. The voice agent's BrowserSpecialist calls these tools
one at a time during a multi-step web task — same Manus pattern that
replaced the flaky `browser_task` (browser-use library) path.

Bridge URL is configurable via `JARVIS_BRIDGE_URL` (default
`http://localhost:8765`). Per-call timeout configurable via
`JARVIS_EXT_TIMEOUT_MS` env or per-tool `_timeout_ms` arg.

Action naming: the bridge dispatches by bare action name (e.g.
`click`, `navigate`, `get_cookies`) — the `ext_` prefix on the
content-side handler names is the JS internal convention. The
function names below match the bridge action names verbatim so
the LLM's tool choice and the bridge's dispatch are perfectly aligned.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

import aiohttp
from livekit.agents import function_tool

logger = logging.getLogger("jarvis-agent.browser-ext")

_BRIDGE_URL = os.environ.get("JARVIS_BRIDGE_URL", "http://localhost:8765")
_DEFAULT_TIMEOUT_MS = int(os.environ.get("JARVIS_EXT_TIMEOUT_MS", "10000"))


async def _post(action: str, **args: Any) -> dict:
    """Post a command to the bridge. Returns the bridge's JSON response
    verbatim — usually `{ok: bool, ...}`. Network/extension errors
    surface as `{ok: False, error: "..."}` so the LLM gets actionable
    text rather than a Python exception."""
    timeout_ms = args.pop("_timeout_ms", None) or _DEFAULT_TIMEOUT_MS
    confirmed = args.pop("_confirmed", False)
    payload = {
        "action": action,
        "args": args,
        "timeout_ms": timeout_ms,
        "confirmed": confirmed,
    }
    # Add 5s slack for HTTP overhead so the bridge's own timeout fires
    # first and we get its structured 504 instead of an aiohttp raise.
    http_timeout = aiohttp.ClientTimeout(total=(timeout_ms / 1000.0) + 5.0)
    try:
        async with aiohttp.ClientSession(timeout=http_timeout) as s:
            async with s.post(
                f"{_BRIDGE_URL}/api/ext_browse",
                json=payload,
            ) as r:
                try:
                    data = await r.json()
                except Exception:
                    text = await r.text()
                    data = {"ok": False, "error": f"non-json response (status={r.status}): {text[:200]}"}
                if not data.get("ok") and r.status >= 500:
                    logger.warning(f"[browser-ext] {action} → status={r.status} {data}")
                return data
    except Exception as e:
        return {"ok": False, "error": f"bridge unreachable: {e}"}


def _summarize(result: dict, max_chars: int = 800) -> str:
    """Convert the bridge's structured response to a string the LLM can
    voice. The browser specialist's prompt expects one short sentence,
    so we trim verbose payloads (DOM summaries, page text) here rather
    than relying on the LLM's discipline."""
    if not result.get("ok"):
        return f"Browser command failed: {result.get('error', 'unknown error')}"
    # Most results have a `value` or `summary` field; fall back to repr.
    for key in ("summary", "value", "text", "url", "result"):
        if key in result and result[key] is not None:
            v = str(result[key])
            return v if len(v) <= max_chars else v[:max_chars] + "…"
    # Drop the `ok` flag and dump what's left
    rest = {k: v for k, v in result.items() if k != "ok"}
    return str(rest)[:max_chars] if rest else "Done."


# ── Navigation (5) ────────────────────────────────────────────────────


@function_tool
async def ext_navigate(url: str) -> str:
    """Navigate the active tab to `url`. Use for "go to X.com" /
    "open this URL" requests. Returns the new URL the tab landed on.

    Args:
        url: Full URL including protocol (https://example.com).
    """
    return _summarize(await _post("navigate", url=url))


@function_tool
async def ext_back() -> str:
    """Go back one step in the active tab's history. Equivalent to
    pressing the browser back button."""
    return _summarize(await _post("back"))


@function_tool
async def ext_forward() -> str:
    """Go forward one step in the active tab's history. Equivalent to
    pressing the browser forward button."""
    return _summarize(await _post("forward"))


@function_tool
async def ext_get_url() -> str:
    """Return the URL of the currently active tab. Use for "where am
    I?" or "what page is this?" requests, or as a sanity check
    after navigation."""
    return _summarize(await _post("get_url"))


@function_tool
async def ext_close_tab() -> str:
    """Close the active tab. Use after the user is done with a task on
    a specific page."""
    return _summarize(await _post("close_tab"))


# ── Reading the page (4) ──────────────────────────────────────────────


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
    return _summarize(await _post("extract_text", selector=selector or ""))


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
    return _summarize(await _post("find_by_text", text=text))


@function_tool
async def ext_dom_summary() -> str:
    """High-level summary of the active tab's DOM — list of forms,
    primary buttons, headings, and visible inputs. Use when the user
    asks "what can I do here?" or you need to plan a multi-step
    interaction. Output is trimmed to keep the LLM context small."""
    return _summarize(await _post("dom_summary"))


@function_tool
async def ext_screenshot() -> str:
    """Capture a screenshot of the active tab. Returns a description
    if the bridge/LLM does vision; otherwise returns a confirmation.
    Use when the page is heavily JS-rendered and DOM-only inspection
    isn't enough."""
    return _summarize(await _post("screenshot"))


# ── Mouse (5) ─────────────────────────────────────────────────────────


@function_tool
async def ext_click(selector: str) -> str:
    """Left-click the element matching `selector`. Use for buttons,
    links, checkboxes — any clickable element with a stable selector.

    Args:
        selector: CSS selector, e.g. `button[type="submit"]`,
                  `a.signin-link`, `#login-btn`. Find one with
                  ext_find_by_text or ext_dom_summary first if you
                  don't know it.
    """
    return _summarize(await _post("click", selector=selector))


@function_tool
async def ext_right_click(selector: str) -> str:
    """Right-click (open the context menu) the element matching
    `selector`. Useful for image-save / copy-link / inspect flows."""
    return _summarize(await _post("right_click", selector=selector))


@function_tool
async def ext_hover(selector: str) -> str:
    """Hover the mouse over `selector` — fires hover-only menus and
    tooltip-revealed actions."""
    return _summarize(await _post("hover", selector=selector))


@function_tool
async def ext_drag(from_selector: str, to_selector: str) -> str:
    """Drag the element matching `from_selector` onto `to_selector`.
    Use for drag-drop UIs (Trello cards, file uploaders that reject
    paste, kanban boards).

    Args:
        from_selector: source element CSS selector.
        to_selector: destination element CSS selector.
    """
    return _summarize(await _post(
        "drag", from_selector=from_selector, to_selector=to_selector,
    ))


@function_tool
async def ext_select(selector: str, value: str) -> str:
    """Set the value of a `<select>` dropdown by its `value` attribute.

    Args:
        selector: CSS selector of the <select> element.
        value: the `value` attribute of the option to choose.
    """
    return _summarize(await _post("select", selector=selector, value=value))


# ── Keyboard / forms (4) ──────────────────────────────────────────────


@function_tool
async def ext_type(selector: str, text: str) -> str:
    """Type `text` into the input/textarea matching `selector`.
    Replaces existing content. Use for filling search boxes, login
    fields, message composers.

    Args:
        selector: CSS selector of the input or textarea.
        text: the text to type. Will be sent as a single string;
              keystroke-level events may not fire for JS-heavy sites
              (in which case use ext_keypress).
    """
    return _summarize(await _post("type", selector=selector, text=text))


@function_tool
async def ext_fill_form(fields: dict) -> str:
    """Fill multiple form fields at once. `fields` maps CSS selectors
    to values; each is filled in sequence. Useful for login / signup
    forms.

    Args:
        fields: dict[str, str] mapping selector → value, e.g.
                `{"#email": "user@x.com", "#password": "..."}`.
    """
    return _summarize(await _post("fill_form", fields=fields))


@function_tool
async def ext_keypress(key: str) -> str:
    """Send a single key press to whatever has focus. Use for special
    keys: `Enter`, `Tab`, `Escape`, `ArrowDown`, `ArrowUp`, etc.

    Args:
        key: KeyboardEvent.key value — `Enter`, `Tab`, `Escape`, etc.
    """
    return _summarize(await _post("keypress", key=key))


@function_tool
async def ext_submit(selector: str) -> str:
    """Submit the form matching `selector`. Equivalent to clicking the
    form's submit button or pressing Enter inside an input.

    Args:
        selector: CSS selector of the <form> element.
    """
    return _summarize(await _post("submit", selector=selector))


# ── Scroll / waiting / dialogs (4) ────────────────────────────────────


@function_tool
async def ext_scroll(direction: str = "down", amount: int = 500) -> str:
    """Scroll the page. Use to reveal content below the fold or scroll
    a feed.

    Args:
        direction: "up" | "down" | "top" | "bottom". Defaults to "down".
        amount: pixels to scroll for "up"/"down". Ignored for "top"/"bottom".
    """
    return _summarize(await _post("scroll", direction=direction, amount=amount))


@function_tool
async def ext_wait_for(selector: str, timeout_ms: int = 5000) -> str:
    """Wait for `selector` to appear in the DOM. Use after navigation
    or any action that loads content asynchronously.

    Args:
        selector: CSS selector to wait for.
        timeout_ms: how long to wait before giving up. Default 5s.
    """
    return _summarize(await _post(
        "wait_for", selector=selector, _timeout_ms=timeout_ms + 1000,
    ))


@function_tool
async def ext_accept_dialog(accept: bool = True) -> str:
    """Accept or dismiss the next browser dialog (confirm, prompt,
    alert). Some sites pop confirms before destructive actions —
    arm this BEFORE clicking the trigger.

    Args:
        accept: True to accept (OK), False to dismiss (Cancel).
    """
    return _summarize(await _post("accept_dialog", accept=accept))


@function_tool
async def ext_switch_iframe(selector: str) -> str:
    """Switch the active context to an iframe matching `selector`.
    Subsequent click/type/extract calls operate inside that iframe.
    Pass an empty string to switch back to the main document.

    Args:
        selector: iframe CSS selector, or "" to return to the top-level.
    """
    return _summarize(await _post("switch_iframe", selector=selector))


# ── Power tools (3) ───────────────────────────────────────────────────


@function_tool
async def ext_exec_js(code: str, _confirmed: bool = False) -> str:
    """Execute arbitrary JavaScript in the active tab and return the
    result. **Destructive verb gate applies.** First call returns a
    confirmation prompt; voice it and re-call with `_confirmed=True`
    only after explicit user OK.

    Args:
        code: the JS expression to evaluate. Must return a JSON-
              serializable value.
        _confirmed: set True only after explicit user confirmation.
    """
    return _summarize(await _post("exec_js", code=code, _confirmed=_confirmed))


@function_tool
async def ext_get_cookies(domain: Optional[str] = None) -> str:
    """List cookies for `domain` (or the active tab's domain if
    omitted). Returns a summary, NOT the raw cookie values, to avoid
    accidentally voicing a session token.

    Args:
        domain: optional domain to scope the lookup.
    """
    return _summarize(await _post("get_cookies", domain=domain or ""))


@function_tool
async def ext_set_cookies(cookies: list, _confirmed: bool = False) -> str:
    """Set cookies on the active tab's domain. **Destructive verb
    gate applies** — the first call returns a confirmation prompt.

    Args:
        cookies: list of {name, value, domain?, path?, expires?} dicts.
        _confirmed: set True only after explicit user confirmation.
    """
    return _summarize(await _post(
        "set_cookies", cookies=cookies, _confirmed=_confirmed,
    ))


# ── Public surface ────────────────────────────────────────────────────


# All 25 tools, in the order the prompt references them. Specialists
# pull this in via their tool_factory.
ALL_TOOLS = [
    # Navigation
    ext_navigate, ext_back, ext_forward, ext_get_url, ext_close_tab,
    # Reading
    ext_extract_text, ext_find_by_text, ext_dom_summary, ext_screenshot,
    # Mouse
    ext_click, ext_right_click, ext_hover, ext_drag, ext_select,
    # Keyboard / forms
    ext_type, ext_fill_form, ext_keypress, ext_submit,
    # Scroll / wait / dialog / iframe
    ext_scroll, ext_wait_for, ext_accept_dialog, ext_switch_iframe,
    # Power tools
    ext_exec_js, ext_get_cookies, ext_set_cookies,
]
