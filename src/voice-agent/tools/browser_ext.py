"""Browser-extension @function_tools — re-export shim.

Pre-2026-05-10 this file held all 38 tools + the bridge POST helper
in a single 746-line module. Split into 5 modules by responsibility
(Step 7 of the audit — browser_ext regrouping):

  - `tools._browser_ext_base` — `post()` / `summarize()` / bridge
    URL+auth constants used by every tool below
  - `tools.browser_ext_nav`      — navigation (6) + search shortcut (1)
  - `tools.browser_ext_query`    — page query + observation (6)
  - `tools.browser_ext_interact` — mouse + keyboard + scroll/wait (15)
  - `tools.browser_ext_state`    — file I/O + storage + power tools (9)

Why split: 38 tools in one file made adding / debugging a specific
behavior class (e.g. "what's the storage-state surface look like?")
require a 700-line scroll. The base/responsibility split puts
related tools next to each other and keeps the shared infrastructure
(`post` + `summarize`) in one canonical place.

Why a re-export shim: jarvis_agent + the browser specialists
register tools via `from tools.browser_ext import ext_navigate,
ext_click, ...`. Re-exporting under the original names keeps every
call site working without modification.

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

# Re-export the bridge primitives under their legacy underscored
# names so external callers / tests that imported them from
# tools.browser_ext keep working.
from tools._browser_ext_base import (
    BRIDGE_URL          as _BRIDGE_URL,
    DEFAULT_TIMEOUT_MS  as _DEFAULT_TIMEOUT_MS,
    LOCAL_TOKEN         as _LOCAL_TOKEN,
    post                as _post,
    summarize           as _summarize,
)

# Re-export every @function_tool under its original name. The 4
# sub-modules each define a subset; here we collapse them back into
# a single public surface.
from tools.browser_ext_nav import (
    ext_navigate,
    ext_new_tab,
    ext_back,
    ext_forward,
    ext_get_url,
    ext_close_tab,
    ext_list_tabs,
    web_search,
)
from tools.browser_ext_query import (
    ext_extract_text,
    ext_find_by_text,
    ext_dom_summary,
    ext_screenshot,
    ext_get_console,
    ext_observe,
)
from tools.browser_ext_interact import (
    ext_click,
    ext_right_click,
    ext_hover,
    ext_drag,
    ext_select,
    ext_type,
    ext_fill_form,
    ext_keypress,
    ext_submit,
    ext_get_dropdown_options,
    ext_scroll,
    ext_wait_for,
    ext_wait_for_load,
    ext_accept_dialog,
    ext_switch_iframe,
)
from tools.browser_ext_state import (
    ext_save_pdf,
    ext_upload_file,
    ext_download_file,
    ext_get_cookies,
    ext_set_cookies,
    ext_local_storage,
    ext_storage_state_get,
    ext_storage_state_set,
    ext_exec_js,
)


__all__ = [
    "ext_navigate", "ext_new_tab", "ext_back", "ext_forward",
    "ext_get_url", "ext_close_tab", "ext_list_tabs", "web_search",
    "ext_extract_text", "ext_find_by_text", "ext_dom_summary",
    "ext_screenshot", "ext_get_console", "ext_observe",
    "ext_click", "ext_right_click", "ext_hover", "ext_drag", "ext_select",
    "ext_type", "ext_fill_form", "ext_keypress", "ext_submit",
    "ext_get_dropdown_options",
    "ext_scroll", "ext_wait_for", "ext_wait_for_load",
    "ext_accept_dialog", "ext_switch_iframe",
    "ext_save_pdf", "ext_upload_file", "ext_download_file",
    "ext_get_cookies", "ext_set_cookies",
    "ext_local_storage", "ext_storage_state_get", "ext_storage_state_set",
    "ext_exec_js",
    "ALL_TOOLS",
]


# All 38 tools (26 base + 4 Phase A + 4 Phase B + 3 Phase C + 1 search
# shortcut), in the order the prompt references them. Specialists pull
# this in via tool_factory.
ALL_TOOLS = [
    # Search shortcut (1) — try first for any "search X for Y" request
    web_search,
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
