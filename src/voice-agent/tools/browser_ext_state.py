"""Browser-extension @function_tools — file I/O + storage + power tools.

Hoisted from `tools/browser_ext.py` 2026-05-10 (Step 7 of the audit
— browser_ext regrouping). Each @function_tool here is a thin
wrapper around the shared `_browser_ext_base.post` helper; the
4-way split groups them by responsibility so the LLM-facing API
surface is easier to navigate when adding new tools or debugging
a specific behavior class.
"""
from __future__ import annotations

import json
from typing import Optional
from livekit.agents import function_tool

from tools._browser_ext_base import post, summarize


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
    return summarize(await post("exec_js", code=code, confirmed=confirmed))


@function_tool
async def ext_get_cookies(domain: Optional[str] = None) -> str:
    """List cookies for `domain` (or the active tab's domain if
    omitted). Returns a summary, NOT the raw cookie values, to avoid
    accidentally voicing a session token.

    Args:
        domain: optional domain to scope the lookup.
    """
    return summarize(await post("get_cookies", domain=domain or ""))


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
    return summarize(await post(
        "set_cookies", cookies=cookies, confirmed=confirmed,
    ))


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
    return summarize(await post("save_pdf", path=path or ""))


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
    return summarize(await post(
        "upload_file", selector=selector, file_path=file_path
    ))


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
    return summarize(await post(
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
    return summarize(await post(
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
    return summarize(await post("storage_state_set", state=state))


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
    return summarize(await post(
        "download_file", url=url, filename=filename or ""
    ))


__all__ = ['ext_exec_js', 'ext_get_cookies', 'ext_set_cookies', 'ext_save_pdf', 'ext_upload_file', 'ext_local_storage', 'ext_storage_state_get', 'ext_storage_state_set', 'ext_download_file']
