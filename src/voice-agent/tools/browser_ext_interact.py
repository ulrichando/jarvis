"""Browser-extension @function_tools — mouse + keyboard + scroll/wait interaction.

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
async def ext_click(selector: str) -> str:
    """Left-click the element matching `selector`. Use for buttons,
    links, checkboxes — any clickable element with a stable selector.

    Args:
        selector: CSS selector, e.g. `button[type="submit"]`,
                  `a.signin-link`, `#login-btn`. Find one with
                  ext_find_by_text or ext_dom_summary first if you
                  don't know it.
    """
    return summarize(await post("click", selector=selector))


@function_tool
async def ext_right_click(selector: str) -> str:
    """Right-click (open the context menu) the element matching
    `selector`. Useful for image-save / copy-link / inspect flows."""
    return summarize(await post("right_click", selector=selector))


@function_tool
async def ext_hover(selector: str) -> str:
    """Hover the mouse over `selector` — fires hover-only menus and
    tooltip-revealed actions."""
    return summarize(await post("hover", selector=selector))


@function_tool
async def ext_drag(from_selector: str, to_selector: str) -> str:
    """Drag the element matching `from_selector` onto `to_selector`.
    Use for drag-drop UIs (Trello cards, file uploaders that reject
    paste, kanban boards).

    Args:
        from_selector: source element CSS selector.
        to_selector: destination element CSS selector.
    """
    return summarize(await post(
        "drag", from_selector=from_selector, to_selector=to_selector,
    ))


@function_tool
async def ext_select(selector: str, value: str) -> str:
    """Set the value of a `<select>` dropdown by its `value` attribute.

    Args:
        selector: CSS selector of the <select> element.
        value: the `value` attribute of the option to choose.
    """
    return summarize(await post("select", selector=selector, value=value))


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
    return summarize(await post("type", selector=selector, text=text))


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
    return summarize(await post("fill_form", fields=fields))


@function_tool
async def ext_keypress(key: str) -> str:
    """Send a single key press to whatever has focus. Use for special
    keys: `Enter`, `Tab`, `Escape`, `ArrowDown`, `ArrowUp`, etc.

    Args:
        key: KeyboardEvent.key value — `Enter`, `Tab`, `Escape`, etc.
    """
    return summarize(await post("keypress", key=key))


@function_tool
async def ext_submit(selector: str) -> str:
    """Submit the form matching `selector`. Equivalent to clicking the
    form's submit button or pressing Enter inside an input.

    Args:
        selector: CSS selector of the <form> element.
    """
    return summarize(await post("submit", selector=selector))


@function_tool
async def ext_scroll(direction: str = "down", amount: int = 500) -> str:
    """Scroll the page. Use to reveal content below the fold or scroll
    a feed.

    Args:
        direction: "up" | "down" | "top" | "bottom". Defaults to "down".
        amount: pixels to scroll for "up"/"down". Ignored for "top"/"bottom".
    """
    return summarize(await post("scroll", direction=direction, amount=amount))


@function_tool
async def ext_wait_for(selector: str, timeout_ms: int = 5000) -> str:
    """Wait for `selector` to appear in the DOM. Use after navigation
    or any action that loads content asynchronously.

    Args:
        selector: CSS selector to wait for.
        timeout_ms: how long to wait before giving up. Default 5s.
    """
    return summarize(await post(
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
    return summarize(await post("accept_dialog", accept=accept))


@function_tool
async def ext_switch_iframe(selector: str) -> str:
    """Switch the active context to an iframe matching `selector`.
    Subsequent click/type/extract calls operate inside that iframe.
    Pass an empty string to switch back to the main document.

    Args:
        selector: iframe CSS selector, or "" to return to the top-level.
    """
    return summarize(await post("switch_iframe", selector=selector))


@function_tool
async def ext_get_dropdown_options(selector: str) -> str:
    """Enumerate the options of a `<select>` element (mirror of
    browser-use's `get_dropdown_options`). Use BEFORE ext_select to
    confirm which option values exist, especially when the LLM is
    guessing at a value name.

    Args:
        selector: CSS selector of the `<select>` element.
    """
    return summarize(await post(
        "get_dropdown_options", selector=selector
    ))


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
    return summarize(await post(
        "wait_for_load", state=state or "load", timeout_ms=timeout_ms
    ))


__all__ = ['ext_click', 'ext_right_click', 'ext_hover', 'ext_drag', 'ext_select', 'ext_type', 'ext_fill_form', 'ext_keypress', 'ext_submit', 'ext_scroll', 'ext_wait_for', 'ext_accept_dialog', 'ext_switch_iframe', 'ext_get_dropdown_options', 'ext_wait_for_load']
