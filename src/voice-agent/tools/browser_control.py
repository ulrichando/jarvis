"""``browser_control`` tool — drive the user's LIVE, VISIBLE browser by keystroke.

This is the FAST path for acting on the browser window the user is actually
looking at: open/close/switch tabs, navigate the current tab, scroll, find on
page, and read the current tab's title/URL. It works by focusing the visible
browser window and injecting xdotool keystrokes — the same mechanism a human
uses (Ctrl+T, Ctrl+W, Ctrl+L, …).

Contrast with the other browser tools:

  * ``browser_task`` drives a SEPARATE *headless* browser (its own throwaway
    profile, no visible window). Use it for background web *results*, never for
    "my open browser".
  * ``computer_use`` SEES the screen (vision→plan→act) and can do the same
    things, but it's a heavyweight LLM-vision loop — overkill for a single
    keystroke. It remains the fallback when an action needs visual confirmation
    (a dialog intercepted the key, the page didn't have focus, etc.).

All desktop I/O goes through :mod:`tools.desktop_control` (the platform-
dispatched xdotool/pywinauto surface), so no new xdotool plumbing lives here.
Like the rest of the desktop surface this is **X11 only** and **blind** — keys
are sent without seeing the result. Every failure degrades to a clear string;
no exception ever propagates into the voice turn.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import time
import urllib.request
from typing import Optional, Tuple

from . import desktop_control
from .registry import registry, tool_error, tool_result

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# WM_CLASS alternation for the common browsers. POSIX ERE (xdotool's regex has
# no inline (?i) flag), so each name lists both cases via a leading char class.
# Mirrors the _TERMINAL_CLASS_RE pattern in jarvis_agent.py. ``--onlyvisible``
# in the search already excludes the headless browser-use/playwright Chromes.
_BROWSER_CLASS_RE = (
    r"("
    r"[Cc]hrome|[Cc]hromium|[Bb]rave|[Ff]irefox|[Nn]avigator"
    r"|[Mm]icrosoft-edge|[Ee]dge|[Vv]ivaldi|[Oo]pera"
    r")"
)

# Trailing " - <Browser Brand>[ …]" suffix on a browser window title, stripped
# to recover the page title for ``current_tab``.
_TITLE_SUFFIX_RE = re.compile(
    r"\s[-–—]\s"
    r"(Google Chrome|Chromium|Mozilla Firefox|Firefox|Brave|Microsoft.?Edge"
    r"|Vivaldi|Opera).*$"
)

# Settle delay between a focus-changing key (Ctrl+T / Ctrl+L / Ctrl+F) and the
# subsequent typed text, so the omnibox / find bar is ready to receive it. The
# only thing desktop_control doesn't do for us.
_SETTLE_S = 0.18

# Scroll direction → xdotool keysym sent to the focused page.
_SCROLL_KEYS = {"down": "Next", "up": "Prior", "top": "Home", "bottom": "End"}

# Per-action timeout for a clipboard helper subprocess (URL read).
_CLIP_TIMEOUT = 2.0


def _parse_cdp_port() -> int:
    """Chrome DevTools port for reading the live tab list. Env-overridable."""
    raw = (os.environ.get("JARVIS_CHROME_CDP_PORT") or "9222").strip()
    try:
        return int(raw)
    except ValueError:
        return 9222


# Reading the live browser's TAB LIST is impossible via keystrokes — the only
# reliable channel is the Chrome DevTools Protocol, open only if Chrome was
# launched with --remote-debugging-port=<port>. Closed → list_tabs returns a
# clear "reopen Chrome" message instead of a guess.
_CDP_PORT = _parse_cdp_port()
_CDP_TIMEOUT = 2.0


# ---------------------------------------------------------------------------
# Availability gate (registry check_fn)
# ---------------------------------------------------------------------------


def _browser_control_available() -> bool:
    """True iff an X11 display is reachable and ``xdotool`` is installed.

    Mirrors ``computer_use_backend.x11_backend_available`` without importing it
    (keeps this module's import cheap). Keeps the tool inert on headless / CI.
    """
    return bool(os.environ.get("DISPLAY", "").strip()) and shutil.which("xdotool") is not None


# ---------------------------------------------------------------------------
# Window resolution
# ---------------------------------------------------------------------------


def _resolve_browser_window() -> Tuple[Optional[int], str]:
    """Find the browser window to act on. Returns ``(window_id, error_msg)``.

    Strategy: enumerate visible browser windows by WM_CLASS; if the currently
    active window is one of them, prefer it (the user is looking at it),
    otherwise take the most-recent in xdotool's stacking order. On no match /
    missing binary, ``window_id`` is None and ``error_msg`` is user-readable.
    """
    ok, out = desktop_control.xdotool_call(
        ["search", "--onlyvisible", "--class", _BROWSER_CLASS_RE]
    )
    if not ok:
        # xdotool exits 1 with empty stdout on no match; the helper maps that to
        # (False, "<stderr or 'xdotool exited 1'>"). Distinguish a missing binary.
        if "not installed" in out or "not available" in out.lower():
            return None, "(xdotool not installed)"
        return None, "(no visible browser window found — open one first)"

    ids = [s for s in out.split() if s.strip().isdigit()]
    if not ids:
        return None, "(no visible browser window found — open one first)"

    ok_active, active_out = desktop_control.xdotool_call(["getactivewindow"])
    active = active_out.strip() if ok_active else ""
    target_str = active if active in ids else ids[-1]
    try:
        return int(target_str), ""
    except ValueError:
        return None, "(no visible browser window found — open one first)"


# ---------------------------------------------------------------------------
# Clipboard helpers (best-effort URL read — xclip or xsel)
# ---------------------------------------------------------------------------


def _clip_tool() -> Optional[str]:
    """Return the clipboard binary name (xclip/xsel) or None if neither exists."""
    return shutil.which("xclip") or shutil.which("xsel")


def _clip_get(tool_path: str) -> Optional[str]:
    """Read the X clipboard via xclip/xsel. None on any failure."""
    name = os.path.basename(tool_path)
    argv = (
        [tool_path, "-selection", "clipboard", "-o"]
        if name == "xclip"
        else [tool_path, "--clipboard", "--output"]
    )
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=_CLIP_TIMEOUT)
        return proc.stdout if proc.returncode == 0 else None
    except Exception:  # noqa: BLE001 — clipboard read must never raise into the turn
        return None


def _clip_set(tool_path: str, value: str) -> None:
    """Restore the X clipboard via xclip/xsel. Silent on failure."""
    name = os.path.basename(tool_path)
    argv = (
        [tool_path, "-selection", "clipboard"]
        if name == "xclip"
        else [tool_path, "--clipboard", "--input"]
    )
    try:
        subprocess.run(argv, input=value, text=True, timeout=_CLIP_TIMEOUT)
    except Exception:  # noqa: BLE001 — best-effort restore
        pass


def _read_current_url() -> Optional[str]:
    """Best-effort read of the focused tab's URL via the omnibox + clipboard.

    Focuses the address bar (Ctrl+L), copies it (Ctrl+C), reads the clipboard,
    then Escapes back to the page WITHOUT navigating and restores the previous
    clipboard contents. Returns None when no clipboard tool is available or any
    step fails. Inherently a hack — the only no-CDP way to get the live URL.
    """
    clip = _clip_tool()
    if not clip:
        return None
    saved = _clip_get(clip)
    try:
        if not desktop_control.send_keys("ctrl+l"):
            return None
        time.sleep(_SETTLE_S)
        desktop_control.send_keys("ctrl+c")
        time.sleep(_SETTLE_S)
        url = _clip_get(clip)
        desktop_control.send_keys("Escape")  # leave the omnibox without navigating
        return (url or "").strip() or None
    finally:
        if saved is not None:
            _clip_set(clip, saved)


# ---------------------------------------------------------------------------
# CDP tab reading — the only reliable way to enumerate the live browser's tabs
# ---------------------------------------------------------------------------


def _cdp_list_pages(port: int = _CDP_PORT) -> Optional[list]:
    """Return the live browser's open page-tabs via Chrome DevTools Protocol.

    GETs ``http://127.0.0.1:<port>/json/list`` and keeps only ``type=="page"``
    targets (real tabs, across every window of that Chrome) — excludes service
    workers, extension background pages, and devtools targets. Returns None when
    the port is closed (Chrome not launched with --remote-debugging-port) or on
    any request/parse error; the caller turns None into a clear "reopen Chrome"
    message. Never raises.
    """
    url = f"http://127.0.0.1:{port}/json/list"
    try:
        with urllib.request.urlopen(url, timeout=_CDP_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001 — closed port / refused / bad JSON → None
        return None
    if not isinstance(data, list):
        return None
    return [
        t for t in data
        if isinstance(t, dict)
        and t.get("type") == "page"
        and not str(t.get("url", "")).startswith("devtools://")
    ]


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

_VALID_ACTIONS = (
    "new_tab",
    "open_url",
    "close_tab",
    "next_tab",
    "prev_tab",
    "goto_tab",
    "scroll",
    "find",
    "current_tab",
    "list_tabs",
)


def browser_control(
    action: str,
    url: Optional[str] = None,
    query: Optional[str] = None,
    direction: Optional[str] = None,
    index: Optional[int] = None,
) -> str:
    """Drive the user's live, visible browser window via keystrokes.

    See the module docstring + schema for the action surface. Returns a JSON
    string (``ok``/``detail`` or ``error``). Never raises.
    """
    action = (action or "").strip().lower()
    if action not in _VALID_ACTIONS:
        return tool_error(
            f"unknown action {action!r}; expected one of {', '.join(_VALID_ACTIONS)}"
        )

    # list_tabs reads over CDP — no window focus needed, so handle it first.
    if action == "list_tabs":
        pages = _cdp_list_pages()
        if pages is None:
            return tool_error(
                f"Can't read your tabs — Chrome's debug port ({_CDP_PORT}) isn't "
                "reachable, so I can't see your live tab list."
            )
        titles = [str(p.get("title") or "(untitled)").strip() for p in pages]
        preview = "; ".join(titles[:10]) + (" …" if len(titles) > 10 else "")
        return tool_result({
            "ok": True,
            "action": "list_tabs",
            "count": len(pages),
            "tabs": titles,
            "detail": f"{len(pages)} tab(s) open" + (f": {preview}" if preview else ""),
        })

    target, err = _resolve_browser_window()
    if target is None:
        return tool_error(err)

    # Bring the chosen window to the foreground so keystrokes land in it.
    # --sync (inside activate_window) blocks until the WM grants focus.
    if not desktop_control.activate_window(target):
        return tool_error("(could not focus the browser window)")
    time.sleep(_SETTLE_S)

    # ---- read action -------------------------------------------------------
    if action == "current_tab":
        ok, name = desktop_control.xdotool_call(["getwindowname", str(target)])
        title = _TITLE_SUFFIX_RE.sub("", name.strip()) if ok else ""
        url_now = _read_current_url()
        detail = title or "(untitled)"
        result = {"ok": True, "action": action, "title": title}
        if url_now:
            result["url"] = url_now
            detail = f"{detail} — {url_now}"
        else:
            result["url_note"] = "URL unavailable (install xclip/xsel for URL read)"
        result["detail"] = detail
        return tool_result(result)

    # ---- keystroke actions -------------------------------------------------
    def _ok(detail: str) -> str:
        return tool_result({"ok": True, "action": action, "detail": detail})

    if action == "new_tab":
        if not desktop_control.send_keys("ctrl+t"):
            return tool_error("(failed to send Ctrl+T)")
        u = (url or "").strip()
        if u:
            time.sleep(_SETTLE_S)
            desktop_control.type_text(u)
            desktop_control.send_keys("Return")
            return _ok(f"opened a new tab → {u}")
        return _ok("opened a new tab")

    if action == "open_url":
        u = (url or "").strip()
        if not u:
            return tool_error("open_url requires a 'url'")
        if not desktop_control.send_keys("ctrl+l"):
            return tool_error("(failed to focus the address bar)")
        time.sleep(_SETTLE_S)
        desktop_control.type_text(u)
        desktop_control.send_keys("Return")
        return _ok(f"navigated current tab → {u}")

    if action == "close_tab":
        if not desktop_control.send_keys("ctrl+w"):
            return tool_error("(failed to send Ctrl+W)")
        return _ok("closed the current tab")

    if action == "next_tab":
        desktop_control.send_keys("ctrl+Next")
        return _ok("switched to the next tab")

    if action == "prev_tab":
        desktop_control.send_keys("ctrl+Prior")
        return _ok("switched to the previous tab")

    if action == "goto_tab":
        try:
            n = int(index)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return tool_error("goto_tab requires an integer 'index' (1-9)")
        if not 1 <= n <= 9:
            return tool_error("goto_tab 'index' must be 1-9 (9 = last tab)")
        desktop_control.send_keys(f"ctrl+{n}")
        return _ok(f"switched to tab {n}" + (" (last)" if n == 9 else ""))

    if action == "scroll":
        d = (direction or "down").strip().lower()
        key = _SCROLL_KEYS.get(d)
        if key is None:
            return tool_error(
                f"scroll 'direction' must be one of {', '.join(_SCROLL_KEYS)}"
            )
        desktop_control.send_keys(key)
        return _ok(f"scrolled {d}")

    if action == "find":
        q = (query or "").strip()
        if not q:
            return tool_error("find requires a non-empty 'query'")
        if not desktop_control.send_keys("ctrl+f"):
            return tool_error("(failed to open find bar)")
        time.sleep(_SETTLE_S)
        desktop_control.type_text(q)
        return _ok(f"searching the page for {q!r}")

    # Unreachable (action validated above), but keep the contract total.
    return tool_error(f"unhandled action {action!r}")


# ---------------------------------------------------------------------------
# Schema + registration
# ---------------------------------------------------------------------------

_TOOL_DESCRIPTION = """\
Control the user's LIVE, VISIBLE browser window (the one they're looking at) by \
keystroke — open/close/switch tabs, navigate, scroll, find on page, and read the \
current tab. FAST and direct; this is the right tool whenever the user means \
"my browser" / "this tab" / "the page I have open".

Use this for: "open a new tab", "open YouTube in a new tab", "close this tab", \
"go to github.com in my browser", "next/previous tab", "switch to tab 3", \
"scroll down", "find <word> on this page", "what page am I on", "how many tabs \
do I have open / list my tabs".

NOT this tool: a headless background web lookup whose RESULT is reported back \
→ use browser_task. Something that needs SEEING the screen first (a dialog, an \
ambiguous click target, verifying the result visually) → use computer_use.

actions:
  new_tab     — Ctrl+T; optional url opens in the new tab
  open_url    — navigate the CURRENT tab to url
  close_tab   — Ctrl+W
  next_tab / prev_tab — switch tabs
  goto_tab    — jump to tab by index (1-8; 9 = last)
  scroll      — direction: up | down | top | bottom
  find        — open find bar and search for query
  current_tab — read the current tab's title (and URL if available)
  list_tabs   — count + list ALL open tabs (needs Chrome's debug port)

Blind + X11 only: keys are sent without seeing the result; if nothing seems to \
happen, fall back to computer_use.
"""

_BROWSER_CONTROL_SCHEMA = {
    "name": "browser_control",
    "description": _TOOL_DESCRIPTION,
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": list(_VALID_ACTIONS),
                "description": "Which browser action to perform.",
            },
            "url": {
                "type": "string",
                "description": "URL (or omnibox search text) for new_tab / open_url.",
            },
            "query": {
                "type": "string",
                "description": "Text to search for on the page (find action).",
            },
            "direction": {
                "type": "string",
                "enum": ["up", "down", "top", "bottom"],
                "description": "Scroll direction (scroll action).",
            },
            "index": {
                "type": "integer",
                "minimum": 1,
                "maximum": 9,
                "description": "Tab number for goto_tab (1-8; 9 = last tab).",
            },
        },
        "required": ["action"],
    },
}


def _handle(args: dict, **_kw) -> str:
    return browser_control(
        action=args.get("action", ""),
        url=args.get("url"),
        query=args.get("query"),
        direction=args.get("direction"),
        index=args.get("index"),
    )


registry.register(
    name="browser_control",
    schema=_BROWSER_CONTROL_SCHEMA,
    handler=_handle,
    toolset="browser",
    check_fn=_browser_control_available,
    is_async=False,
    description=_BROWSER_CONTROL_SCHEMA["description"],
    emoji="🧭",
    max_result_size_chars=4_000,
)
