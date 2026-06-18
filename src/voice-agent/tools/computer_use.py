"""``computer_use`` tool — JARVIS-native Linux/X11 desktop control.

Ported from the upstream computer-use toolset (which was macOS-only, driving
``cua-driver`` over MCP). This is the *primitive action surface* — screenshot,
mouse, keyboard, scroll, drag, window introspection — exposed as a single
consolidated tool with an ``action`` discriminator. It is NOT a self-contained
vision-plan-act loop: the upstream tool had no in-tool LLM call either. It
returns a screenshot + a textual summary, and the supervisor LLM (which is
vision-capable) does the planning across turns. See "Vision/plan loop" below.

Backend
-------
Input is driven by ``xdotool`` via subprocess; screenshots by ``mss`` (or
ImageMagick ``import`` as a fallback). See :mod:`tools.computer_use_backend`.
``pyautogui`` / ``python-xlib`` / ``pynput`` are deliberately NOT used — they
are not installed on this host.

SOM overlays & element-index targeting (2026-05-31)
---------------------------------------------------
``capture(mode='som')`` renders numbered red/orange bounding-box overlays
on each window from the ``wmctrl`` window list and returns the annotated
screenshot. The 1-based element index from the overlay can be used directly
in click / scroll / drag actions instead of guessing pixel coordinates:

  * ``click element=3`` — click the center of window 3
  * ``scroll element=2 direction='down'`` — scroll over window 2
  * ``drag from_element=1 to_element=5`` — drag window 1 to window 5

Element-index targeting is more reliable than pixel coordinates because it
clicks the exact center of the element's bounding box, which doesn't drift
when window positions change. See ``_render_som_overlays`` in the backend
for the overlay implementation (PIL/ImageDraw). ``capture(mode='vision')``
returns a clean screenshot with no overlays.

Gating
------
The registry ``check_fn`` (:func:`check_computer_use_requirements`) returns
True only when an X11 display is reachable AND ``xdotool`` is installed. In a
headless / CI environment it returns False, so the tool registers inert and
the adapter skips it — tests never drive X11. The dispatch path also re-checks
availability defensively before touching the backend.

Vision/plan loop
----------------
The upstream package returned a multimodal tool-message (text + base64 image)
that its agent runtime spliced into the model context. JARVIS's tool adapter
str-coerces handler results to a single string, and the voice supervisor's
multimodal tool-result plumbing is a separate, larger piece of work. So this
port returns a JSON summary (mode/size/window-list, plus an image-bytes count)
rather than feeding the raw screenshot pixels back into the LLM. The screenshot
IS captured and its size reported; wiring the pixels into the supervisor's
context is DEFERRED — tracked in the module docstring rather than half-built.
Today the tool is most useful for the deterministic actions (click at known
coordinates / type / key / scroll / focus a window / list windows). A fuller
vision loop can layer the image plumbing on later without changing this surface.

Safety
------
Read-only actions (``capture`` / ``wait`` / ``list_apps``) run freely.
Mutating actions go through an optional approval callback (default-allow when
none is wired, matching the upstream contract — the voice agent's own gating
sits one layer out). Destructive shell text in ``type`` and destructive key
combos in ``key`` are hard-blocked regardless of approval.
"""
from __future__ import annotations

import itertools
import json
import logging
import os
import re
import subprocess
import threading
from typing import Any, Dict, List, Optional, Tuple

from tools.computer_use_backend import (
    ActionResult,
    CaptureResult,
    ComputerUseBackend,
    NoopBackend,
    UIElement,
    X11ComputerUseBackend,
    x11_backend_available,
)
from tools.registry import registry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schema — one consolidated tool with an ``action`` discriminator.
# ---------------------------------------------------------------------------

COMPUTER_USE_SCHEMA: Dict[str, Any] = {
    "name": "computer_use",
    "description": (
        "Take real actions on the user's Linux X11 desktop: open apps, "
        "click, type, scroll, navigate, and read the screen. This is the "
        "PRIMARY tool for any request that requires a visible effect — "
        "opening browser tabs, navigating to URLs, interacting with "
        "windows, menus, forms, dialogs, or reading what is on screen.\n"
        "\n"
        "WHEN TO USE (always invoke this tool — do not reply with text "
        "alone):\n"
        "  - User asks to open / launch / navigate / go to / show / "
        "click / type / scroll / focus / minimize / close something.\n"
        "  - User asks to interact with a visible application or "
        "website.\n"
        "  - User asks 'what's on my screen?', 'what does it say?', 'is "
        "the build done?' — anything that needs you to read the screen.\n"
        "  - User asks to control the browser (open new tab, navigate to "
        "a URL, search).\n"
        "\n"
        "DO NOT reply with 'Done', 'On it', 'I've opened it', 'It's "
        "loading', 'Let me focus Chrome' UNLESS you have already issued "
        "the corresponding computer_use call in this same turn and a "
        "follow-up capture confirms the effect. Claiming success without "
        "invoking this tool is a hard failure: the user sees nothing "
        "happen and has to ask again. Tool first, words after.\n"
        "\n"
        "PREFERRED WORKFLOW (element mode — MOST RELIABLE):\n"
        "  1. action='capture' mode='som' — captures the screen with\n"
        "     numbered red/orange overlays on every window. Each window\n"
        "     has a number; use its index for click/scroll/drag.\n"
        "  2. Plan the next step from the numbered overlay on the\n"
        "     screenshot.\n"
        "  3. Execute via element=12 (click the center of window 12),\n"
        "     or from_element=5 to_element=8 (drag from window 5 to\n"
        "     window 8). Element = 1-based index from the SOM capture.\n"
        "  4. After every mutating action the post-action screen is\n"
        "     captured AUTOMATICALLY and attached to your next turn —\n"
        "     do not spend a capture call just to see the result.\n"
        "     Recapture with mode='som' only when you need fresh\n"
        "     element numbers (windows opened/closed/moved).\n"
        "\n"
        "FALLBACK WORKFLOW (pixel coordinates — when SOM isn't fresh):\n"
        "  1. action='capture' mode='vision' to see the raw screen.\n"
        "  2. Plan the next step from what you see.\n"
        "  3. Execute via action='focus_app' / 'click' / 'type' / 'key' "
        "/ 'scroll' using pixel coordinates = [x, y] from the screenshot.\n"
        "  4. action='capture' again to verify the effect.\n"
        "\n"
        "COMMON RECIPES:\n"
        "  - Open a URL in the user's already-running browser (e.g. "
        "Chrome): focus_app app='Chrome'  →  key keys='ctrl+t'  →  type "
        "text='instagram.com'  →  key keys='Return'.\n"
        "  - Bring a minimized window forward: focus_app "
        "app='<title substring>'.\n"
        "  - Read a visible dialog: action='capture' first, then "
        "describe what's there from the screenshot.\n"
        "  - Click a specific window by its SOM number: click "
        "element=3.\n"
        "  - See what's running: action='list_apps' returns the window "
        "list.\n"
        "  - Launch an app that ISN'T running yet: focus_app only "
        "activates existing windows (uses wmctrl -a). To start a new "
        "application, use the `terminal` tool with `setsid <app> &` "
        "first, then come back here to interact with it.\n"
        "\n"
        "READING SMALL TEXT (when a normal capture is too small to read):\n"
        "  - Can't make out file names, tab titles, status-bar text, line "
        "numbers, menu labels, or a button caption? DON'T guess. Re-capture "
        "with region=[x1, y1, x2, y2] (top-left → bottom-right pixels of just "
        "that area) — it returns that region at full 1:1 resolution with no "
        "downscale, so small text becomes legible.\n"
        "\n"
        "TRICKY UI — PREFER THE KEYBOARD:\n"
        "  - Dropdowns, scrollbars, native menus, and small toggles are often "
        "unreliable to hit by click. Prefer the keyboard: arrow keys to move, "
        "Tab to advance fields, Return to confirm, Escape to cancel, or "
        "type-to-filter inside a list. Reach for key/type before fiddly pixel "
        "clicks.\n"
        "\n"
        "VERIFY EACH STEP:\n"
        "  - After every mutating action the result screen is auto-attached "
        "to your next turn. READ it and confirm the intended effect actually "
        "happened BEFORE you move on or report success. If it didn't work, "
        "adjust and retry — never assume an action succeeded.\n"
        "\n"
        "Linux/X11 only. Target UI elements by their 1-based element\n"
        "index from a SOM capture (\"click element=12\"), or by pixel\n"
        "coordinate (\"click coordinate=[500, 300]\"). Element-index\n"
        "clicking is more reliable because it clicks the center of the\n"
        "element's bounding box instead of guessing pixels. Requires\n"
        "xdotool.\n"
        "\n"
        "When NOT to use: for web lookups or web navigation where "
        "nothing needs to appear on the user's own screen, prefer "
        "`browser_task` (headless, DOM-aware, more reliable). Use "
        "computer_use for the VISIBLE desktop — showing something on "
        "screen, controlling a native GUI app, or when the user "
        "explicitly wants to watch it happen."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "capture",
                    "click",
                    "double_click",
                    "right_click",
                    "middle_click",
                    "triple_click",
                    "left_mouse_down",
                    "left_mouse_up",
                    "drag",
                    "scroll",
                    "type",
                    "key",
                    "hold_key",
                    "key_down",
                    "key_up",
                    "wait",
                    "list_apps",
                    "list_available_apps",
                    "focus_app",
                    "launch",
                    "dismiss_popup",
                    "close_window",
                    "vision_analyze",
                    "mouse_move",
                    "cursor_position",
                ],
                "description": (
                    "Which action to perform. 'capture', 'wait', and "
                    "'list_apps' are read-only and always allowed. The rest "
                    "move/click/type on the real desktop."
                ),
            },
            "mode": {
                "type": "string",
                "enum": ["som", "vision", "ax"],
                "description": (
                    "Capture mode. 'som' (default) renders numbered red/orange "
                    "overlays on every window and returns them with the screenshot "
                    "— use the element index for click/scroll/drag targeting. "
                    "'vision' is a plain screenshot with no overlays. 'ax' returns "
                    "just the window list (no screenshot). Prefer 'som' for "
                    "element-index targeting, 'vision' for clean verification."
                ),
            },
            "app": {
                "type": "string",
                "description": (
                    "Optional. Limit capture/focus to windows whose title "
                    "matches this substring."
                ),
            },
            "coordinate": {
                "type": "array",
                "items": {"type": "integer"},
                "minItems": 2,
                "maxItems": 2,
                "description": "Pixel coordinates [x, y] for click/scroll.",
            },
            "element": {
                "type": "integer",
                "description": (
                    "1-based window index from a SOM capture. Instead of guessing "
                    "pixel coordinates, click/scroll the center of the numbered "
                    "window. Takes priority over coordinate when both are present. "
                    "Example: element=12 clicks the center of window 12."
                ),
            },
            "from_element": {
                "type": "integer",
                "description": (
                    "1-based source window index for drag (replaces "
                    "from_coordinate when present). Must be from a recent SOM "
                    "capture."
                ),
            },
            "to_element": {
                "type": "integer",
                "description": (
                    "1-based target window index for drag (replaces to_coordinate "
                    "when present). Must be from a recent SOM capture."
                ),
            },
            "button": {
                "type": "string",
                "enum": ["left", "right", "middle"],
                "description": "Mouse button. Defaults to left.",
            },
            "modifiers": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": ["ctrl", "shift", "alt", "super", "cmd", "option"],
                },
                "description": (
                    "Modifier keys held during the action ('cmd'/'option' map "
                    "to Super/Alt on Linux)."
                ),
            },
            "from_coordinate": {
                "type": "array",
                "items": {"type": "integer"},
                "minItems": 2,
                "maxItems": 2,
                "description": "Drag source [x, y].",
            },
            "to_coordinate": {
                "type": "array",
                "items": {"type": "integer"},
                "minItems": 2,
                "maxItems": 2,
                "description": "Drag target [x, y].",
            },
            "direction": {
                "type": "string",
                "enum": ["up", "down", "left", "right"],
                "description": "Scroll direction.",
            },
            "amount": {
                "type": "integer",
                "description": "Scroll wheel ticks. Default 3.",
            },
            "text": {
                "type": "string",
                "description": "Text to type (action='type').",
            },
            "keys": {
                "type": "string",
                "description": (
                    "Key combo for action='key', e.g. 'ctrl+s', 'alt+Tab', "
                    "'Return', 'Escape'. Use '+' to combine."
                ),
            },
            "seconds": {
                "type": "number",
                "description": "Seconds to wait (action='wait') or hold (action='hold_key'). Max 30.",
            },
            "command": {
                "type": "string",
                "description": (
                    "Binary name or full path to launch (action='launch'). "
                    "Uses setsid to detach from the voice-agent process, so "
                    "the app survives the turn. The app is focused "
                    "automatically after launch. Prefer the simple binary "
                    "name (e.g. 'thunar', 'firefox', 'gnome-terminal') — "
                    "use list_available_apps to discover what's installed."
                ),
            },
            "raise_window": {
                "type": "boolean",
                "description": "action='focus_app' only. Accepted for parity.",
            },
            "region": {
                "type": "array",
                "items": {"type": "integer"},
                "minItems": 4,
                "maxItems": 4,
                "description": (
                    "Capture a sub-region of the screen [x1, y1, x2, y2] at 1:1 "
                    "pixel mapping (no downscale). Useful for reading small text "
                    "or inspecting UI details at full resolution. Only applies "
                    "to action='capture'."
                ),
            },
            "name": {
                "type": "string",
                "description": (
                    "Window title substring to close (action='close_window'). "
                    "Uses wmctrl -c for a polite close that apps can intercept "
                    "(unsaved-changes prompts, etc.). When omitted, sends Alt+F4 "
                    "to the currently-focused window — for error dialogs, prefer "
                    "dismiss_popup instead."
                ),
            },
        },
        "required": ["action"],
    },
}


# ---------------------------------------------------------------------------
# Approval & safety (ported from the upstream tool; macOS combos rewritten to
# their Linux equivalents).
# ---------------------------------------------------------------------------

_approval_callback = None


def set_approval_callback(cb) -> None:
    """Register a callback for computer_use approval prompts.

    The callback receives (action, args, summary) and returns one of:
    ``"approve_once"`` | ``"approve_session"`` | ``"always_approve"`` | ``"deny"``.
    """
    global _approval_callback
    _approval_callback = cb


_SAFE_ACTIONS = frozenset({"capture", "wait", "list_apps", "list_available_apps",
                         "vision_analyze", "cursor_position"})

_DESTRUCTIVE_ACTIONS = frozenset({
    "click", "double_click", "right_click", "middle_click", "triple_click",
    "left_mouse_down", "left_mouse_up",
    "drag", "scroll", "type", "key", "hold_key", "key_down", "key_up",
    "focus_app", "launch", "dismiss_popup", "close_window", "mouse_move",
})

# ── Permission tiers (Gap 9) ───────────────────────────────────────
# Controlled via JARVIS_COMPUTER_USE_TIER env var. Default "full" (no
# restriction). Operators can lock down to:
#   view     — read-only screen/window introspection (safe actions only)
#   interact — view + mouse actions only (no keyboard — type/key/hold_key
#              are blocked, preventing destructive shell commands)
#   full     — all actions (default)
# Keyboard actions excluded from the "interact" tier.
_KEYBOARD_ACTIONS = frozenset({"type", "key", "hold_key", "key_down", "key_up", "close_window"})

# Actions allowed in the "interact" tier: everything except keyboard.
_TIER_INTERACT = _SAFE_ACTIONS | (_DESTRUCTIVE_ACTIONS - _KEYBOARD_ACTIONS)


def _resolve_tier() -> str:
    """Return the effective permission tier (view|interact|full)."""
    tier = os.environ.get("JARVIS_COMPUTER_USE_TIER", "full").strip().lower()
    if tier in ("view", "interact", "full"):
        return tier
    logger.warning("Unknown JARVIS_COMPUTER_USE_TIER=%r, treating as full", tier)
    return "full"


def _action_allowed_in_tier(action: str, tier: str) -> bool:
    """Return True if *action* is permitted at the given *tier*."""
    if tier == "full":
        return True
    if tier == "view":
        return action in _SAFE_ACTIONS
    if tier == "interact":
        return action in _TIER_INTERACT
    return True  # defensive — unknown tier, allow


# ── Desktop environment detection ────────────────────────────────────
# Maps DE names to their canonical default apps. The supervisor uses this
# to know what's actually installed instead of guessing from training data
# (which defaults to GNOME/Nautilus).
_DE_DEFAULTS: Dict[str, Dict[str, str]] = {
    "xfce":    {"file_manager": "thunar",   "terminal": "xfce4-terminal",
                 "browser": "firefox",       "settings": "xfce4-settings-manager"},
    "gnome":   {"file_manager": "nautilus", "terminal": "gnome-terminal",
                 "browser": "firefox",       "settings": "gnome-control-center"},
    "kde":     {"file_manager": "dolphin",  "terminal": "konsole",
                 "browser": "firefox",       "settings": "systemsettings"},
    "plasma":  {"file_manager": "dolphin",  "terminal": "konsole",
                 "browser": "firefox",       "settings": "systemsettings"},
    "lxde":    {"file_manager": "pcmanfm",  "terminal": "lxterminal",
                 "browser": "firefox",       "settings": "lxappearance"},
    "lxqt":    {"file_manager": "pcmanfm-qt", "terminal": "qterminal",
                 "browser": "firefox",       "settings": "lxqt-config"},
    "cinnamon": {"file_manager": "nemo",    "terminal": "gnome-terminal",
                 "browser": "firefox",       "settings": "cinnamon-settings"},
    "mate":    {"file_manager": "caja",     "terminal": "mate-terminal",
                 "browser": "firefox",       "settings": "mate-control-center"},
    "budgie":  {"file_manager": "nautilus", "terminal": "gnome-terminal",
                 "browser": "firefox",       "settings": "gnome-control-center"},
    "deepin":  {"file_manager": "dde-file-manager", "terminal": "deepin-terminal",
                 "browser": "firefox",       "settings": "dde-control-center"},
}

_DE_CACHE: Optional[Dict[str, Any]] = None


def _detect_desktop_environment() -> Dict[str, Any]:
    """Return the detected desktop environment and its default apps.
    Cached — the DE doesn't change during a session."""
    global _DE_CACHE
    if _DE_CACHE is not None:
        return _DE_CACHE
    de_name = (
        os.environ.get("XDG_CURRENT_DESKTOP", "") or
        os.environ.get("XDG_SESSION_DESKTOP", "") or
        os.environ.get("DESKTOP_SESSION", "") or
        ""
    ).strip().lower()
    # Normalize compound values like "XFCE" or "ubuntu:GNOME"
    if ":" in de_name:
        de_name = de_name.split(":")[-1]
    de_key = de_name
    defaults = _DE_DEFAULTS.get(de_key, {})
    _DE_CACHE = {
        "desktop_environment": de_name or "unknown",
        "default_apps": defaults,
    }
    return _DE_CACHE


def _remember_app_launch(command: str) -> None:
    """No-op: cross-session app→binary learning is currently retired.

    This used to publish a ``memory.value.upserted`` event to
    ``pipeline.memory_extractor`` — a module removed in the Hermes teardown
    when JARVIS moved to file-backed memory. The import raised
    ``ModuleNotFoundError`` that the bare ``except`` swallowed, so the
    feature had been silently dead since that removal (no crash, no write,
    no consumer ever read the ``app_launch`` facts back).

    Deliberately NOT repointed at ``file_memory.add(...)``: the three
    file-backed targets (MEMORY/USER/PROCEDURES) are small, curated,
    prompt-injected budgets (``memory`` runs ~2.2 KB and is typically near
    full). Machine trivia like "firefox launches via firefox" would either
    fail to write when full or evict real user facts. Reviving this needs a
    dedicated app-launch cache + a reader in the launch/discovery path —
    feature work, tracked separately, not a drop-in repoint.
    """
    return

# Hard-blocked key combos — destructive regardless of approval level. Linux/X11
# equivalents of the upstream macOS list (logout / lock / kill-session).
_BLOCKED_KEY_COMBOS = {
    frozenset({"ctrl", "alt", "backspace"}),   # zap X server
    frozenset({"ctrl", "alt", "delete"}),       # logout / interrupt on many DEs
    frozenset({"super", "l"}),                  # lock screen (GNOME)
    frozenset({"ctrl", "alt", "l"}),            # lock screen (other DEs)
}

# Normalize toward the Linux-canonical vocabulary the _BLOCKED_KEY_COMBOS sets
# use (alt, super, ctrl) so the subset check below matches regardless of which
# alias the LLM emitted (option->alt, cmd/win/meta->super, control->ctrl).
_KEY_ALIASES = {
    "command": "super", "cmd": "super", "win": "super", "meta": "super",
    "control": "ctrl",
    "option": "alt",
}


def _canon_key_combo(keys: str) -> frozenset:
    parts = [p.strip().lower() for p in re.split(r"\s*\+\s*", keys) if p.strip()]
    return frozenset(_KEY_ALIASES.get(p, p) for p in parts)


_BLOCKED_TYPE_PATTERNS = [
    re.compile(r"curl\s+[^|]*\|\s*bash", re.IGNORECASE),
    re.compile(r"curl\s+[^|]*\|\s*sh", re.IGNORECASE),
    re.compile(r"wget\s+[^|]*\|\s*bash", re.IGNORECASE),
    re.compile(r"\bsudo\s+rm\s+-[rf]", re.IGNORECASE),
    re.compile(r"\brm\s+-rf\s+/\s*$", re.IGNORECASE),
    re.compile(r":\s*\(\)\s*\{\s*:\|:\s*&\s*\}", re.IGNORECASE),  # fork bomb
]


def _is_blocked_type(text: str) -> Optional[str]:
    for pat in _BLOCKED_TYPE_PATTERNS:
        if pat.search(text):
            return pat.pattern
    return None


# ---------------------------------------------------------------------------
# Backend selection — env-swappable for tests
# ---------------------------------------------------------------------------

_backend_lock = threading.Lock()
_backend: Optional[ComputerUseBackend] = None
_session_auto_approve = False
_always_allow: set = set()


def _get_backend() -> ComputerUseBackend:
    global _backend
    with _backend_lock:
        if _backend is None:
            name = os.environ.get("JARVIS_COMPUTER_USE_BACKEND", "x11").lower()
            if name in {"x11", "", "xdotool"}:
                _backend = X11ComputerUseBackend()
            elif name == "noop":
                _backend = NoopBackend()
            else:
                raise RuntimeError(f"Unknown JARVIS_COMPUTER_USE_BACKEND={name!r}")
            _backend.start()
        return _backend


def reset_session_approval() -> None:
    """Clear per-session computer-use approval grants.

    `_session_auto_approve` / `_always_allow` are module-level, so without
    an explicit reset they persist for the whole worker process — a
    reconnect or new conversation would silently inherit an "always
    approve" the user granted in a PRIOR session, letting the agent click
    and type on the desktop autonomously without re-asking. Called at
    entrypoint() session start so each session starts un-approved. Leaves
    the cached backend (display connection) intact — only the consent
    state resets.
    """
    global _session_auto_approve, _always_allow
    _session_auto_approve = False
    _always_allow = set()


def reset_backend_for_tests() -> None:
    """Test helper — tear down the cached backend and approval state."""
    global _backend, _session_auto_approve, _always_allow
    with _backend_lock:
        if _backend is not None:
            try:
                _backend.stop()
            except Exception:
                pass
        _backend = None
    _session_auto_approve = False
    _always_allow = set()


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def handle_computer_use(args: Dict[str, Any], **kwargs) -> str:
    """Main entry point — dispatched by tools.registry.

    Always returns a JSON string (see the module docstring for why the
    multimodal screenshot path is deferred).
    """
    if not isinstance(args, dict):
        args = {}
    action = (args.get("action") or "").strip().lower()
    if not action:
        return json.dumps({"error": "missing `action`"})

    # Permission tier gate (Gap 9). Blocks actions above the configured tier
    # BEFORE any safety check or dispatch. Default "full" = no restriction.
    tier = _resolve_tier()
    if not _action_allowed_in_tier(action, tier):
        return json.dumps({
            "error": f"action {action!r} blocked by tier '{tier}'",
            "hint": (
                f"JARVIS_COMPUTER_USE_TIER={tier} — this action requires a "
                "higher tier. Set to 'full' to allow all actions."
            ),
        })

    # Defensive availability re-check (check_fn gates registration, but the
    # display can vanish between turns).
    if not x11_backend_available():
        return json.dumps({
            "error": "computer_use unavailable",
            "hint": "Requires an X11 DISPLAY and xdotool. Not usable headless.",
        })

    # Safety: validate text/key actions before any approval prompt.
    if action == "type":
        pat = _is_blocked_type(args.get("text", "") or "")
        if pat:
            return json.dumps({
                "error": f"blocked pattern in type text: {pat!r}",
                "hint": "Dangerous shell patterns cannot be typed via computer_use.",
            })

    if action in {"key", "hold_key", "key_down", "key_up"}:
        combo = _canon_key_combo(args.get("keys", "") or "")
        for blocked in _BLOCKED_KEY_COMBOS:
            if blocked and blocked.issubset(combo):
                return json.dumps({
                    "error": f"blocked key combo: {sorted(blocked)}",
                    "hint": "Destructive system shortcuts are hard-blocked.",
                })

    if action in _DESTRUCTIVE_ACTIONS:
        err = _request_approval(action, args)
        if err is not None:
            return err

    try:
        backend = _get_backend()
    except Exception as e:
        return json.dumps({"error": f"computer_use backend unavailable: {e}"})

    try:
        result = _dispatch(backend, action, args)
    except Exception as e:  # noqa: BLE001 — a tool error must not crash the turn
        logger.exception("computer_use %s failed", action)
        _audit_action(action, args, success=False)
        return json.dumps({"error": f"{action} failed: {e}"})

    # Vision-feedback loop (P2a): record the action label for the recent-actions
    # trail — AFTER dispatch, so only actions that actually ran are recorded.
    # Best-effort — never break the tool.
    try:
        from pipeline import computer_use_vision
        computer_use_vision.record_action(_summarize_action(action, args))
    except Exception:
        pass
    if action in _DESTRUCTIVE_ACTIONS:
        _publish_post_action_frame(backend, action)
    _audit_action(action, args, success=_result_succeeded(result))
    return result


def _publish_post_action_frame(backend: ComputerUseBackend, action: str) -> None:
    """Claude-computer-use parity: after a mutating action, grab a fresh frame
    and publish it to the vision cache so the supervisor SEES the result of
    its own action in the next generation — no extra capture call needed.

    Uses the backend's raw ``_screenshot_b64`` grab (NOT ``capture()``):
    a ``mode='vision'`` capture clears the SOM element cache, which would
    break som→click(element)→click(element) chains. Backends without the
    raw grab (e.g. NoopBackend) silently skip. Best-effort; disable with
    ``JARVIS_CU_AUTO_SCREENSHOT=0``.
    """
    if os.environ.get("JARVIS_CU_AUTO_SCREENSHOT", "1").strip().lower() in {"0", "false", "off"}:
        return
    grab = getattr(backend, "_screenshot_b64", None)
    if grab is None:
        return
    try:
        png_b64, width, height = grab()
        if not png_b64:
            return
        from pipeline import computer_use_vision
        computer_use_vision.publish_capture(
            png_b64=png_b64, width=width, height=height,
            action_label=f"after {action}",
        )
    except Exception:
        pass


# Monotonic step counter for the audit trail (per process).
_AUDIT_STEP = itertools.count(1)


def _result_succeeded(result: str) -> bool:
    """Did a dispatched action succeed, per its JSON result string?

    Two failure shapes exist: an error envelope ``{"error": ...}`` (schema
    rejects / blocked) and a failed ``ActionResult`` rendered as
    ``{"ok": false, ...}`` (e.g. element index out of range — xdotool never
    ran). Treat BOTH as failures for the audit row; default True for any
    non-dict / unparseable result so a healthy action is never mis-flagged.
    """
    try:
        data = json.loads(result)
    except Exception:
        return True
    if not isinstance(data, dict):
        return True
    if "error" in data:
        return False
    if data.get("ok") is False:
        return False
    return True


def _redact_audit_params(args: Dict[str, Any]) -> Dict[str, Any]:
    """Copy of args safe to persist: typed text may contain secrets
    (passwords typed into GUI password fields), so only its length is
    recorded — never the content."""
    out = {k: v for k, v in args.items() if k != "text" and v is not None}
    if isinstance(args.get("text"), str):
        out["text_chars"] = len(args["text"])
    return out


def _audit_action(action: str, args: Dict[str, Any], *, success: bool) -> None:
    """Append a destructive action to the ``computer_use_actions`` audit
    table. Best-effort — the tool must never fail because the audit DB is
    locked/missing (the writer swallows its own sqlite errors too).

    The table + writer predate this surface (the retired in-tool loop
    called it); the direct-tool port had dropped the wiring, leaving real
    mouse/keyboard actions with no persistent audit row.
    """
    if action not in _DESTRUCTIVE_ACTIONS:
        return
    try:
        from pipeline import turn_telemetry as _tt

        _tt.log_computer_use_action(
            # Read DEFAULT_DB_PATH at call time (module attribute) so test
            # monkeypatching and JARVIS_TELEMETRY_PATH are honored.
            db_path=_tt.DEFAULT_DB_PATH,
            handoff_id="direct",
            step=next(_AUDIT_STEP),
            model_used=None,
            action=action,
            params_json=json.dumps(_redact_audit_params(args), ensure_ascii=False),
            success=success,
        )
    except Exception:
        pass


def _request_approval(action: str, args: Dict[str, Any]) -> Optional[str]:
    """Return None if approved, or a JSON error string if denied."""
    global _session_auto_approve, _always_allow
    if _session_auto_approve:
        return None
    if action in _always_allow:
        return None
    cb = _approval_callback
    if cb is None:
        # No approval wired — default allow. The voice agent's gating sits one
        # layer out (matches the upstream contract).
        return None
    summary = _summarize_action(action, args)
    try:
        verdict = cb(action, args, summary)
    except Exception as e:
        logger.warning("approval callback failed: %s", e)
        verdict = "deny"
    if verdict == "approve_once":
        return None
    if verdict in {"approve_session", "always_approve"}:
        _always_allow.add(action)
        if verdict == "always_approve":
            _session_auto_approve = True
        return None
    return json.dumps({"error": "denied by user", "action": action})


def _summarize_action(action: str, args: Dict[str, Any]) -> str:
    if action in {"click", "double_click", "right_click", "middle_click", "triple_click"}:
        elem = args.get("element")
        if elem is not None:
            return f"{action} element={elem}"
        coord = args.get("coordinate")
        return f"{action} at {tuple(coord)}" if coord else action
    if action == "left_mouse_down":
        elem = args.get("element")
        if elem is not None:
            return f"mouse_down element={elem}"
        coord = args.get("coordinate")
        return f"mouse_down at {tuple(coord)}" if coord else "mouse_down"
    if action == "left_mouse_up":
        elem = args.get("element")
        if elem is not None:
            return f"mouse_up element={elem}"
        coord = args.get("coordinate")
        return f"mouse_up at {tuple(coord)}" if coord else "mouse_up"
    if action == "drag":
        fe = args.get("from_element")
        te = args.get("to_element")
        if fe is not None or te is not None:
            return f"drag element {fe} -> {te}" if fe and te else f"drag element={fe or te}"
        return f"drag {args.get('from_coordinate')} -> {args.get('to_coordinate')}"
    if action == "scroll":
        elem = args.get("element")
        if elem is not None:
            return f"scroll element={elem} {args.get('direction', '?')} x{args.get('amount', 3)}"
        return f"scroll {args.get('direction', '?')} x{args.get('amount', 3)}"
    if action == "type":
        text = args.get("text", "")
        return f"type {text[:60]!r}" + ("..." if len(text) > 60 else "")
    if action == "key":
        return f"key {args.get('keys', '')!r}"
    if action == "hold_key":
        keys = args.get("keys", "")
        secs = args.get("seconds", 1.0)
        return f"hold_key {keys!r} for {secs}s"
    if action == "key_down":
        return f"key_down {args.get('keys', '')!r}"
    if action == "key_up":
        return f"key_up {args.get('keys', '')!r}"
    if action == "mouse_move":
        elem = args.get("element")
        if elem is not None:
            return f"mouse_move element={elem}"
        coord = args.get("coordinate")
        return f"mouse_move to {tuple(coord)}" if coord else "mouse_move"
    if action == "focus_app":
        return f"focus {args.get('app', '')!r}"
    if action == "launch":
        return f"launch {args.get('command', '')!r}"
    if action == "dismiss_popup":
        return "dismiss_popup"
    if action == "close_window":
        name = args.get("name", "")
        return f"close_window {name!r}" if name else "close_window (focused)"
    if action == "list_available_apps":
        return "list_available_apps"
    if action == "vision_analyze":
        app = args.get("app") or ""
        return f"vision_analyze" + (f" ({app})" if app else "")
    return action


# ── Dialog / popup window detection ────────────────────────────────────
# Used by dismiss_popup to target the RIGHT window instead of blindly
# firing Escape+Alt+F4 at whatever has focus (which is how VS Code gets
# closed by mistake when a popup appears).

# Title keywords for dialog-likelihood scoring.
_DIALOG_KEYWORDS: Dict[str, int] = {
    # High confidence (score 3) — almost certainly a dialog
    "error": 3, "warning": 3, "crash report": 3, "authentication required": 3,
    # Medium confidence (score 2) — likely a dialog or prompt
    "alert": 2, "confirm": 2, "dialog": 2, "popup": 2, "question": 2,
    "prompt": 2, "unsaved": 2, "permission": 2,
    # Low confidence (score 1) — could be part of a regular window title
    "message": 1, "notice": 1, "notification": 1, "save": 1,
    "close": 1, "exit": 1, "password": 1,
}


def _get_screen_size() -> Tuple[int, int]:
    """Get screen dimensions via xdotool. Returns (1920, 1080) on failure."""
    try:
        result = subprocess.run(
            ["xdotool", "getdisplaygeometry"],
            capture_output=True, text=True, timeout=2,
        )
        if result.returncode == 0:
            parts = result.stdout.strip().split()
            if len(parts) >= 2:
                return int(parts[0]), int(parts[1])
    except Exception:
        pass
    return 1920, 1080


def _check_window_is_dialog(wid: int) -> bool:
    """Check _NET_WM_WINDOW_TYPE for DIALOG or POPUP_MENU via xprop.
    Returns False on any failure (missing xprop, window gone, etc.)."""
    try:
        result = subprocess.run(
            ["xprop", "-id", f"0x{wid:x}", "_NET_WM_WINDOW_TYPE"],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode == 0:
            out_lower = result.stdout.lower()
            if "_net_wm_window_type_dialog" in out_lower:
                return True
            if "_net_wm_window_type_popup_menu" in out_lower:
                return True
    except Exception:
        pass
    return False


def _find_dialog_windows(apps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Identify likely popup/dialog windows from a window list.

    Uses three heuristics, each contributing to a score:
      1. Title keywords (Error, Warning, Dialog, etc.)
      2. Window size relative to screen (dialogs are small)
      3. _NET_WM_WINDOW_TYPE_DIALOG via xprop (strongest signal)

    Returns windows with score >= 3, sorted highest-first.
    Windows with xprop DIALOG type are always included (score += 5).
    """
    screen_w, screen_h = _get_screen_size()
    screen_area = screen_w * screen_h

    scored: List[Tuple[int, Dict[str, Any]]] = []
    for app in apps:
        title = app.get("title", "").lower()
        bounds = app.get("bounds", [0, 0, 0, 0])
        w, h = bounds[2], bounds[3]
        wid = app.get("window_id", 0)
        score = 0

        # 1. Title keyword scoring
        for kw, weight in _DIALOG_KEYWORDS.items():
            if kw in title:
                score += weight

        # 2. Size heuristic — dialogs are typically <40% of screen
        if w > 0 and h > 0 and screen_area > 0:
            area_ratio = (w * h) / screen_area
            if area_ratio < 0.1:
                score += 2  # tiny window — very likely a dialog
            elif area_ratio < 0.3:
                score += 1  # small window — could be a dialog

        # 3. xprop window type (only for promising candidates — saves
        #    subprocess calls on every window)
        if score >= 2 and wid > 0:
            try:
                if _check_window_is_dialog(wid):
                    score += 5  # definitive: WM says it's a dialog
            except Exception:
                pass

        if score >= 3:
            scored.append((score, app))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [a for _, a in scored]


def _dispatch(backend: ComputerUseBackend, action: str, args: Dict[str, Any]) -> str:
    if action == "capture":
        mode = str(args.get("mode", "som"))
        if mode not in {"som", "vision", "ax"}:
            return json.dumps({"error": f"bad mode {mode!r}; use som|vision|ax"})
        region = args.get("region")
        if region is not None and (not isinstance(region, (list, tuple)) or len(region) != 4):
            return json.dumps({"error": "region must be [x1, y1, x2, y2]"})
        cap = backend.capture(mode=mode, app=args.get("app"), region=region)
        return _capture_response(cap)

    if action == "vision_analyze":
        """Combine a SOM capture with a structured text description of the
        screen state. Returns the element list + window geometry in a text
        summary the supervisor can read directly. Also publishes the frame
        to the vision cache so the supervisor's ``llm_node`` can inject it
        as pixel context. Best-effort — never breaks the turn."""
        cap = backend.capture(mode="som", app=args.get("app"))
        # Build a structured description from the element list.
        desc_parts = [
            f"Screen capture ({cap.width}x{cap.height}) with {len(cap.elements)} window(s)."
        ]
        for el in cap.elements:
            x, y, w, h = el.bounds
            desc_parts.append(
                f"  [{el.index}] \"{el.label}\" at ({x},{y}) → ({x+w},{y+h}) "
                f"({w}x{h}px)"
            )
        description = "\n".join(desc_parts)
        # Publish the SOM frame into the vision cache so the next generation
        # can see the pixels (SOM overlays included).
        try:
            from pipeline import computer_use_vision as _cuv
            _cuv.publish_capture(
                png_b64=cap.png_b64, width=cap.width, height=cap.height,
                action_label="vision_analyze")
        except Exception:
            pass
        payload: Dict[str, Any] = {
            "ok": True,
            "action": "vision_analyze",
            "description": description,
            "window_count": len(cap.elements),
            "width": cap.width,
            "height": cap.height,
        }
        if cap.app:
            payload["app"] = cap.app
        if cap.window_title:
            payload["window_title"] = cap.window_title
        return json.dumps(payload)

    if action == "wait":
        res = backend.wait(float(args.get("seconds", 1.0)))
        return _text_response(res)

    if action == "list_apps":
        apps = backend.list_apps()
        de_info = _detect_desktop_environment()
        return json.dumps({"apps": apps, "count": len(apps), **de_info})

    if action == "list_available_apps":
        apps = backend.list_available_apps()
        de_info = _detect_desktop_environment()
        return json.dumps({"available_apps": apps, "count": len(apps), **de_info})

    if action == "launch":
        command = args.get("command", "").strip()
        if not command:
            return json.dumps({"error": "launch requires `command`"})
        res = backend.launch_app(command)
        result = json.loads(_text_response(res))
        # Best-effort auto-focus after launch: wait briefly and try to
        # activate the new window by the command name.
        if res.ok:
            try:
                import time as _time
                _time.sleep(0.5)
                backend.focus_app(command)
            except Exception:
                pass
            # Procedural memory: remember this app→command mapping so the
            # supervisor doesn't have to be guided again next time.
            _remember_app_launch(command)
        return json.dumps(result)

    if action == "dismiss_popup":
        # Smart popup dismissal — find the dialog window FIRST, then
        # target IT specifically. Never blindly Alt+F4 whatever has
        # focus (that's how VS Code gets closed by mistake).
        #
        # Strategy ladder (all targeted at the dialog, not global):
        #   1. Find dialog → focus → Escape (most dialogs)
        #   2. Still there? → wmctrl -c (polite WM_DELETE_WINDOW)
        #   3. Still there? → focus dialog + Alt+F4 (last resort)
        #   4. No dialog found? → Escape only (safe — never Alt+F4)
        import time as _time

        apps_before = backend.list_apps()
        dialogs = _find_dialog_windows(apps_before)

        if dialogs:
            dlg = dialogs[0]
            dlg_title = dlg.get("title", "")
            dlg_wid = dlg.get("window_id", 0)

            # Strategy 1: focus the dialog, send Escape
            backend.focus_app(dlg_title)
            _time.sleep(0.1)
            backend.key("Escape")
            _time.sleep(0.2)

            # Verify: did the dialog disappear?
            apps_after = backend.list_apps()
            still_there = any(
                a.get("window_id") == dlg_wid
                for a in apps_after
            )
            if not still_there:
                return json.dumps({
                    "ok": True,
                    "action": "dismiss_popup",
                    "message": f"dismissed popup {dlg_title!r} with Escape",
                    "strategy": "Escape",
                })

            # Strategy 2: polite close on the specific dialog window
            backend.close_window(name=dlg_title)
            _time.sleep(0.2)
            apps_after2 = backend.list_apps()
            still_there2 = any(
                a.get("window_id") == dlg_wid
                for a in apps_after2
            )
            if not still_there2:
                return json.dumps({
                    "ok": True,
                    "action": "dismiss_popup",
                    "message": f"dismissed popup {dlg_title!r} with wmctrl -c",
                    "strategy": "wmctrl -c",
                })

            # Strategy 3: focus the dialog and Alt+F4 (targeted — not blind)
            backend.focus_app(dlg_title)
            _time.sleep(0.1)
            backend.key("Alt+F4")
            return json.dumps({
                "ok": True,
                "action": "dismiss_popup",
                "message": f"sent Alt+F4 to popup {dlg_title!r} — capture to verify",
                "strategy": "Alt+F4 (targeted)",
            })

        # No dialog identified — send Escape only (safe fallback).
        # Do NOT Alt+F4 — we don't know what has focus.
        backend.key("Escape")
        return json.dumps({
            "ok": True,
            "action": "dismiss_popup",
            "message": "Escape sent (no dialog window identified — "
                       "capture to verify)",
            "strategy": "Escape (fallback — no dialog found)",
        })

    if action == "close_window":
        # Close a window. When `name` is given, sends WM_DELETE_WINDOW
        # to the first window whose title matches (polite close via
        # wmctrl -c). Without a name, falls back to Alt+F4 on the
        # currently-focused window — use with caution.
        name = args.get("name", "").strip()
        if name:
            res = backend.close_window(name=name)
        else:
            res = backend.close_window()  # Alt+F4 on focused window
        return _text_response(res)

    if action == "focus_app":
        app = args.get("app")
        if not app:
            return json.dumps({"error": "focus_app requires `app`"})
        res = backend.focus_app(app, raise_window=bool(args.get("raise_window")))
        return _text_response(res)

    if action == "cursor_position":
        # Read-only: return current mouse coordinates from xdotool.
        res = backend.get_cursor_position()
        return json.dumps({
            "ok": res.ok,
            "action": "cursor_position",
            "x": res.meta.get("x") if res.ok else None,
            "y": res.meta.get("y") if res.ok else None,
            "message": res.message,
        })

    if action == "mouse_move":
        # Move cursor without clicking — needed for hover, tooltips,
        # and positioning before a separate click action.
        element = args.get("element")
        coord = args.get("coordinate") or (None, None)
        x = coord[0] if coord and len(coord) >= 1 else None
        y = coord[1] if coord and len(coord) >= 2 else None
        res = backend.move_cursor(element=element, x=x, y=y)
        return _text_response(res)

    # ── Auto-focus: if `app` is specified on a mutating action, activate
    # the target window FIRST so the action lands on the right window.
    # This mirrors what Anthropic's computer_use does — every action
    # implicitly targets the focused window; we just make it explicit.
    # Focus failures are logged but don't block the action — the window
    # might already be focused, or wmctrl might not match the title.
    _auto_focus_app = args.get("app")
    if _auto_focus_app and action in {
        "click", "double_click", "right_click", "middle_click", "triple_click",
        "left_mouse_down", "left_mouse_up",
        "drag", "scroll", "type", "key", "hold_key", "key_down", "key_up",
        "mouse_move",
    }:
        try:
            backend.focus_app(str(_auto_focus_app))
        except Exception:
            pass  # focus is best-effort — don't block the action

    if action in {"click", "double_click", "right_click", "middle_click"}:
        button = args.get("button")
        click_count = 1
        if action == "double_click":
            click_count = 2
        elif action == "right_click":
            button = "right"
        elif action == "middle_click":
            button = "middle"
        else:
            button = button or "left"
        element = args.get("element")
        coord = args.get("coordinate") or (None, None)
        x = coord[0] if coord and len(coord) >= 1 else None
        y = coord[1] if coord and len(coord) >= 2 else None
        res = backend.click(
            element=element, x=x, y=y, button=button or "left", click_count=click_count,
            modifiers=args.get("modifiers"),
        )
        return _text_response(res)

    if action == "drag":
        res = backend.drag(
            from_element=args.get("from_element"),
            to_element=args.get("to_element"),
            from_xy=tuple(args["from_coordinate"]) if args.get("from_coordinate") else None,
            to_xy=tuple(args["to_coordinate"]) if args.get("to_coordinate") else None,
            button=args.get("button", "left"),
            modifiers=args.get("modifiers"),
        )
        return _text_response(res)

    if action == "scroll":
        element = args.get("element")
        coord = args.get("coordinate") or (None, None)
        res = backend.scroll(
            element=element,
            direction=args.get("direction", "down"),
            amount=int(args.get("amount", 3)),
            x=coord[0] if coord and len(coord) >= 1 else None,
            y=coord[1] if coord and len(coord) >= 2 else None,
            modifiers=args.get("modifiers"),
        )
        return _text_response(res)

    if action == "type":
        res = backend.type_text(args.get("text", "") or "")
        return _text_response(res)

    if action == "key":
        res = backend.key(args.get("keys", "") or "")
        return _text_response(res)

    if action == "hold_key":
        keys = args.get("keys", "")
        if not keys:
            return json.dumps({"error": "hold_key requires `keys`"})
        seconds = float(args.get("seconds", 1.0))
        res = backend.hold_key(keys, seconds=seconds)
        return _text_response(res)

    if action == "key_down":
        keys = args.get("keys", "")
        if not keys:
            return json.dumps({"error": "key_down requires `keys`"})
        res = backend.key_down(keys)
        return _text_response(res)

    if action == "key_up":
        keys = args.get("keys", "")
        if not keys:
            return json.dumps({"error": "key_up requires `keys`"})
        res = backend.key_up(keys)
        return _text_response(res)

    if action == "triple_click":
        button = args.get("button", "left")
        element = args.get("element")
        coord = args.get("coordinate") or (None, None)
        x = coord[0] if coord and len(coord) >= 1 else None
        y = coord[1] if coord and len(coord) >= 2 else None
        res = backend.click(
            element=element, x=x, y=y, button=button, click_count=3,
            modifiers=args.get("modifiers"),
        )
        return _text_response(res)

    if action == "left_mouse_down":
        button = args.get("button", "left")
        element = args.get("element")
        coord = args.get("coordinate") or (None, None)
        x = coord[0] if coord and len(coord) >= 1 else None
        y = coord[1] if coord and len(coord) >= 2 else None
        res = backend.mouse_down(
            element=element, x=x, y=y, button=button,
            modifiers=args.get("modifiers"),
        )
        return _text_response(res)

    if action == "left_mouse_up":
        button = args.get("button", "left")
        element = args.get("element")
        coord = args.get("coordinate") or (None, None)
        x = coord[0] if coord and len(coord) >= 1 else None
        y = coord[1] if coord and len(coord) >= 2 else None
        res = backend.mouse_up(
            element=element, x=x, y=y, button=button,
            modifiers=args.get("modifiers"),
        )
        return _text_response(res)

    return json.dumps({"error": f"unknown action {action!r}"})


# ---------------------------------------------------------------------------
# Response shaping (text-only; multimodal image path deferred — see module doc)
# ---------------------------------------------------------------------------


def _text_response(res: ActionResult) -> str:
    payload: Dict[str, Any] = {"ok": res.ok, "action": res.action}
    if res.message:
        payload["message"] = res.message
    if res.meta:
        payload["meta"] = res.meta
    return json.dumps(payload)


def _capture_response(cap: CaptureResult) -> str:
    # The `note` field that used to live here ("Screenshot pixels are
    # not yet fed back into the model context (deferred). Use ...
    # screenshot() for vision.") was REMOVED 2026-05-28. The supervisor
    # read that string in the tool response and voiced it back to the
    # user as "the screenshot pixels aren't feeding back" — sounded
    # like a JARVIS bug but was really the tool parroting its own
    # documentation. The screen-share observer
    # (pipeline/screen_share_observer.py) caches a fresh text
    # description while sharing is active; `screen_description` below
    # threads that into the response so the supervisor has the actual
    # screen CONTENT, not a confession about pixel plumbing.
    payload: Dict[str, Any] = {
        "ok": True,
        "action": "capture",
        "mode": cap.mode,
        "width": cap.width,
        "height": cap.height,
        "screenshot_bytes": cap.png_bytes_len,
        "screenshot_captured": cap.png_b64 is not None,
        "elements": [_element_to_dict(e) for e in cap.elements],
    }
    if cap.app:
        payload["app"] = cap.app
    if cap.window_title:
        payload["window_title"] = cap.window_title
    # When the user is screen-sharing, the polling or stream observer
    # has a fresh description ready; forward it so the supervisor
    # doesn't need to call another tool just to "see".
    try:
        from pipeline.screen_share_observer import latest_description_global
        desc = latest_description_global()
        if desc:
            payload["screen_description"] = desc.strip()
    except Exception:
        pass
    # Vision-feedback loop (P2a): publish the frame so JarvisAgent.llm_node can
    # inject it into the next generation. Best-effort — never break the tool.
    try:
        from pipeline import computer_use_vision
        computer_use_vision.publish_capture(
            png_b64=cap.png_b64, width=cap.width, height=cap.height,
            action_label=f"capture/{cap.mode}")
    except Exception:
        pass
    return json.dumps(payload)


def _element_to_dict(e: UIElement) -> Dict[str, Any]:
    return {
        "index": e.index,
        "role": e.role,
        "label": e.label,
        "bounds": list(e.bounds),
        "app": e.app,
        "window_id": e.window_id,
        "pid": e.pid,
    }


# ---------------------------------------------------------------------------
# Availability check (used by the registry check_fn)
# ---------------------------------------------------------------------------


def check_computer_use_requirements() -> bool:
    """Return True iff computer_use can run on this host right now.

    Conditions: a reachable X11 ``$DISPLAY`` AND ``xdotool`` installed. False
    in headless / CI environments, so the tool registers inert there.
    """
    return x11_backend_available()


def get_computer_use_schema() -> Dict[str, Any]:
    return COMPUTER_USE_SCHEMA


# ---------------------------------------------------------------------------
# Registration (self-registering at import — discovered via AST scan)
# ---------------------------------------------------------------------------

registry.register(
    name="computer_use",
    schema=COMPUTER_USE_SCHEMA,
    handler=lambda args, **kw: handle_computer_use(args, **kw),
    toolset="computer_use",
    check_fn=check_computer_use_requirements,
    is_async=False,
    emoji="🖱️",
    description=COMPUTER_USE_SCHEMA["description"],
)


__all__ = [
    "COMPUTER_USE_SCHEMA",
    "handle_computer_use",
    "set_approval_callback",
    "check_computer_use_requirements",
    "get_computer_use_schema",
    "reset_backend_for_tests",
]
