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

═══ Why three params are `*_json: str` instead of dict/list ═══

`ext_fill_form(fields_json)`, `ext_set_cookies(cookies_json)`, and
`ext_storage_state_set(state_json)` take JSON strings rather than
native Python `dict` / `list` types. Reason: Groq strict-mode JSON
schema validation rejects open object schemas with HTTP 400
"`additionalProperties:false` must be set on every object". When
ANY tool in the registered list fails validation, Groq rejects the
entire request, FallbackAdapter switches to DeepSeek (slower), and
the LLM may stream "Done" text before a tool actually fires. We hit
this on 2026-05-02 — user observed "first attempt said done with no
action, second attempt worked." JSON strings sidestep the issue
entirely; the LLM emits a JSON literal and we parse server-side.
Don't revert to native dict/list types unless Groq relaxes strict
mode.
"""
from __future__ import annotations

import json
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
    # Pydantic v2.10+ rejects leading-underscore field names in
    # create_model, so the @function_tool exposes `confirmed` (no
    # underscore). The bridge wire-protocol has always used "confirmed".
    timeout_ms = args.pop("timeout_ms", None) or _DEFAULT_TIMEOUT_MS
    confirmed = args.pop("confirmed", False)
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


# ── Navigation (6) ────────────────────────────────────────────────────


@function_tool
async def ext_navigate(url: str) -> str:
    """Navigate the active tab to `url`. Use for "go to X.com" /
    "open this URL" requests. Returns the new URL the tab landed on.

    Args:
        url: Full URL including protocol (https://example.com).
    """
    return _summarize(await _post("navigate", url=url))


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
    # Optional[str] = None instead of str = "" so Groq strict-mode
    # tool-call validation accepts a call with the property omitted.
    # Live failure 2026-05-02: `tool call validation failed:
    # parameters for tool ext_new_tab did not match schema: errors:
    # [missing properties: 'url']` — Groq required `url` even though
    # it had a default. Fallback to DeepSeek opened a tab; second
    # user request opened ANOTHER one because chat_ctx truncation
    # had hidden the first → the "two tabs" complaint.
    return _summarize(await _post("new_tab", url=url or ""))


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
async def ext_fill_form(fields_json: str) -> str:
    """Fill multiple form fields at once. Each selector→value pair is
    filled in sequence. Useful for login / signup forms.

    Args:
        fields_json: JSON object string mapping selector → value, e.g.
                     `'{"#email": "user@x.com", "#password": "..."}'`.
                     Must be a valid JSON string.
    """
    try:
        fields = json.loads(fields_json) if isinstance(fields_json, str) else fields_json
    except json.JSONDecodeError as e:
        return f"Browser command failed: fields_json is not valid JSON ({e})"
    if not isinstance(fields, dict):
        return "Browser command failed: fields_json must be a JSON object"
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
async def ext_exec_js(code: str, confirmed: bool = False) -> str:
    """Execute arbitrary JavaScript in the active tab and return the
    result. **Destructive verb gate applies.** First call returns a
    confirmation prompt; voice it and re-call with `confirmed=True`
    only after explicit user OK.

    Args:
        code: the JS expression to evaluate. Must return a JSON-
              serializable value.
        confirmed: set True only after explicit user confirmation.
    """
    return _summarize(await _post("exec_js", code=code, confirmed=confirmed))


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
async def ext_set_cookies(cookies_json: str, confirmed: bool = False) -> str:
    """Set cookies on the active tab's domain. **Destructive verb
    gate applies** — the first call returns a confirmation prompt.

    Args:
        cookies_json: JSON array string of cookie objects, e.g.
                      `'[{"name":"sid","value":"abc","domain":".x.com"}]'`.
                      Each object may contain name, value, domain, path, expires.
        confirmed: set True only after explicit user confirmation.
    """
    try:
        cookies = json.loads(cookies_json) if isinstance(cookies_json, str) else cookies_json
    except json.JSONDecodeError as e:
        return f"Browser command failed: cookies_json is not valid JSON ({e})"
    if not isinstance(cookies, list):
        return "Browser command failed: cookies_json must be a JSON array"
    return _summarize(await _post(
        "set_cookies", cookies=cookies, confirmed=confirmed,
    ))


# ── Public surface ────────────────────────────────────────────────────


# Phase A additions (2026-05-02): four gap-fill tools lifted from
# browser-use (MIT) + Playwright MCP (Apache-2.0) patterns. See the
# 2026-05-02 browser-tooling audit doc for the cross-product gap
# table that prioritized these four.


@function_tool
async def ext_list_tabs() -> str:
    """List all open browser tabs (mirror of Playwright MCP's
    `browser_tabs`). Returns a compact summary of each tab's id, url,
    title, and which one is active. Use to answer "what's open?" or
    before switching tabs.
    """
    return _summarize(await _post("list_tabs"))


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
    return _summarize(await _post("get_console", level=level or "", limit=limit))


@function_tool
async def ext_save_pdf(path: Optional[str] = None) -> str:
    """Save the current page as PDF (mirror of Playwright MCP's
    `browser_pdf_save` and browser-use's `save_pdf`). Uses CDP
    Page.printToPDF; saves to the user's Downloads folder unless
    `path` is supplied.

    Args:
        path: Optional filename (or relative path inside Downloads).
              Default = "<page-title>.pdf".
    """
    return _summarize(await _post("save_pdf", path=path or ""))


@function_tool
async def ext_upload_file(selector: str, file_path: str) -> str:
    """Upload a file to a `<input type="file">` element by selector
    (mirror of browser-use's `upload_file`). The file must exist on
    the SAME machine as Chrome. Uses CDP DOM.setFileInputFiles.

    Args:
        selector: CSS selector of the file input
                  (e.g. 'input[type=file]', '#avatar-upload').
        file_path: Absolute path to the file on Chrome's filesystem.
    """
    return _summarize(await _post(
        "upload_file", selector=selector, file_path=file_path
    ))


# ── Phase B: modern-web parity (2026-05-02) ──────────────────────────


@function_tool
async def ext_local_storage(
    action: Optional[str] = None,
    key: Optional[str] = None,
    value: Optional[str] = None,
    scope: Optional[str] = None,
) -> str:
    """Read/write the page's localStorage or sessionStorage (mirror
    of Playwright MCP's `browser_localstorage_*` / `browser_sessionstorage_*`).
    Modern SPAs put auth tokens in localStorage, not cookies — this
    is the 2025 web's equivalent of ext_get_cookies/ext_set_cookies.

    Args:
        action: 'get' | 'set' | 'delete' | 'list' | 'clear'. Default 'list'.
        key: storage key (required for get/set/delete).
        value: storage value (only for set).
        scope: 'local' (persistent) or 'session' (per-tab). Default 'local'.
    """
    return _summarize(await _post(
        "local_storage",
        action=action or "list",
        key=key or "",
        value=value or "",
        scope=scope or "local",
    ))


@function_tool
async def ext_storage_state_get(include_cookies: bool = True) -> str:
    """Snapshot the active tab's full storage state — cookies +
    localStorage + sessionStorage — as a single JSON blob (mirror of
    Playwright MCP's `browser_storage_state`). Useful for "save my
    login state" before navigating away.

    Args:
        include_cookies: include cookies in the snapshot (default: yes).
    """
    return _summarize(await _post(
        "storage_state_get", include_cookies=include_cookies
    ))


@function_tool
async def ext_storage_state_set(state_json: str) -> str:
    """Restore a previously-snapshotted storage state (mirror of
    Playwright MCP's `browser_set_storage_state`). Pair with
    ext_storage_state_get.

    Args:
        state_json: JSON object string with optional keys `cookies`,
                    `localStorage`, `sessionStorage`. Same shape as
                    ext_storage_state_get returns.
    """
    try:
        state = json.loads(state_json) if isinstance(state_json, str) else state_json
    except json.JSONDecodeError as e:
        return f"Browser command failed: state_json is not valid JSON ({e})"
    if not isinstance(state, dict):
        return "Browser command failed: state_json must be a JSON object"
    return _summarize(await _post("storage_state_set", state=state))


@function_tool
async def ext_get_dropdown_options(selector: str) -> str:
    """Enumerate the options of a `<select>` element (mirror of
    browser-use's `get_dropdown_options`). Use BEFORE ext_select to
    confirm which option values exist, especially when the LLM is
    guessing at a value name.

    Args:
        selector: CSS selector of the `<select>` element.
    """
    return _summarize(await _post(
        "get_dropdown_options", selector=selector
    ))


# ── Phase C: advanced (2026-05-02) ───────────────────────────────────


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
    return _summarize(await _post("observe", query=query or "", limit=limit))


@function_tool
async def ext_wait_for_load(state: Optional[str] = None, timeout_ms: int = 10000) -> str:
    """Wait for the page to reach a specific load state (mirror of
    Playwright MCP's `browser_wait_for` extended modes).

    Args:
        state: 'load' (full load complete, default),
               'domcontentloaded' (DOM parsed),
               'networkidle' (no network for 500ms).
        timeout_ms: Max wait (1000-60000, default 10000).
    """
    return _summarize(await _post(
        "wait_for_load", state=state or "load", timeout_ms=timeout_ms
    ))


@function_tool
async def ext_download_file(url: str, filename: Optional[str] = None) -> str:
    """Download a file directly to the Downloads folder (mirror of
    Playwright MCP's download capture + Skyvern's DOWNLOAD_FILE
    action). Pass a direct URL — for "click this button which
    triggers a download" use ext_click; Chrome auto-saves any
    detected download.

    Args:
        url: Direct downloadable URL.
        filename: Optional filename override (default = URL's
                  filename or browser default).
    """
    return _summarize(await _post(
        "download_file", url=url, filename=filename or ""
    ))


# All 37 tools (26 base + 4 Phase A + 4 Phase B + 3 Phase C), in the
# order the prompt references them. Specialists pull this in via
# tool_factory.
ALL_TOOLS = [
    # Navigation (7)
    ext_navigate, ext_new_tab, ext_back, ext_forward, ext_get_url, ext_close_tab,
    ext_list_tabs,
    # Reading + observation (6)
    ext_extract_text, ext_find_by_text, ext_dom_summary, ext_screenshot,
    ext_get_console, ext_observe,
    # Mouse (5)
    ext_click, ext_right_click, ext_hover, ext_drag, ext_select,
    # Keyboard / forms (5)
    ext_type, ext_fill_form, ext_keypress, ext_submit,
    ext_get_dropdown_options,
    # Scroll / wait / dialog / iframe (5)
    ext_scroll, ext_wait_for, ext_wait_for_load,
    ext_accept_dialog, ext_switch_iframe,
    # File I/O (3)
    ext_save_pdf, ext_upload_file, ext_download_file,
    # Storage (5) — cookies + localStorage + storage_state
    ext_get_cookies, ext_set_cookies,
    ext_local_storage, ext_storage_state_get, ext_storage_state_set,
    # Power tools (1)
    ext_exec_js,
]
