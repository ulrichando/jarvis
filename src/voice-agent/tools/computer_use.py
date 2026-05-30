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

import json
import logging
import os
import re
import threading
from typing import Any, Dict, List, Optional

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
        "PREFERRED WORKFLOW:\n"
        "  1. action='capture' to see the screen and the window list.\n"
        "  2. Plan the next step from what you see.\n"
        "  3. Execute via action='focus_app' / 'click' / 'type' / 'key' "
        "/ 'scroll' using pixel coordinates from the screenshot.\n"
        "  4. action='capture' again to verify the effect actually "
        "happened.\n"
        "\n"
        "COMMON RECIPES:\n"
        "  - Open a URL in the user's already-running browser (e.g. "
        "Chrome): focus_app app='Chrome'  →  key keys='ctrl+t'  →  type "
        "text='instagram.com'  →  key keys='Return'.\n"
        "  - Bring a minimized window forward: focus_app "
        "app='<title substring>'.\n"
        "  - Read a visible dialog: action='capture' first, then "
        "describe what's there from the screenshot.\n"
        "  - See what's running: action='list_apps' returns the window "
        "list.\n"
        "  - Launch an app that ISN'T running yet: focus_app only "
        "activates existing windows (uses wmctrl -a). To start a new "
        "application, use the `terminal` tool with `setsid <app> &` "
        "first, then come back here to interact with it.\n"
        "\n"
        "Linux/X11 only. No accessibility tree, so target UI elements "
        "by pixel coordinate read from the latest capture. Coordinates "
        "are screen pixels [x, y]. Requires xdotool.\n"
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
                    "drag",
                    "scroll",
                    "type",
                    "key",
                    "wait",
                    "list_apps",
                    "focus_app",
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
                    "Capture mode. 'vision' (recommended on Linux) is a plain "
                    "screenshot. 'som'/'ax' additionally return the window list; "
                    "X11 has no accessibility tree so there are no numbered "
                    "element overlays."
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
                "description": "Seconds to wait (action='wait'). Max 30.",
            },
            "raise_window": {
                "type": "boolean",
                "description": "action='focus_app' only. Accepted for parity.",
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


_SAFE_ACTIONS = frozenset({"capture", "wait", "list_apps"})

_DESTRUCTIVE_ACTIONS = frozenset({
    "click", "double_click", "right_click", "middle_click",
    "drag", "scroll", "type", "key", "focus_app",
})

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

    if action == "key":
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

    # Vision-feedback loop (P2a): record the action label for the recent-actions
    # trail injected alongside the screenshot. Best-effort — never break the tool.
    try:
        from pipeline import computer_use_vision
        computer_use_vision.record_action(_summarize_action(action, args))
    except Exception:
        pass

    try:
        return _dispatch(backend, action, args)
    except Exception as e:  # noqa: BLE001 — a tool error must not crash the turn
        logger.exception("computer_use %s failed", action)
        return json.dumps({"error": f"{action} failed: {e}"})


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
    if action in {"click", "double_click", "right_click", "middle_click"}:
        coord = args.get("coordinate")
        return f"{action} at {tuple(coord)}" if coord else action
    if action == "drag":
        return f"drag {args.get('from_coordinate')} -> {args.get('to_coordinate')}"
    if action == "scroll":
        return f"scroll {args.get('direction', '?')} x{args.get('amount', 3)}"
    if action == "type":
        text = args.get("text", "")
        return f"type {text[:60]!r}" + ("..." if len(text) > 60 else "")
    if action == "key":
        return f"key {args.get('keys', '')!r}"
    if action == "focus_app":
        return f"focus {args.get('app', '')!r}"
    return action


def _dispatch(backend: ComputerUseBackend, action: str, args: Dict[str, Any]) -> str:
    if action == "capture":
        mode = str(args.get("mode", "vision"))
        if mode not in {"som", "vision", "ax"}:
            return json.dumps({"error": f"bad mode {mode!r}; use som|vision|ax"})
        cap = backend.capture(mode=mode, app=args.get("app"))
        return _capture_response(cap)

    if action == "wait":
        res = backend.wait(float(args.get("seconds", 1.0)))
        return _text_response(res)

    if action == "list_apps":
        apps = backend.list_apps()
        return json.dumps({"apps": apps, "count": len(apps)})

    if action == "focus_app":
        app = args.get("app")
        if not app:
            return json.dumps({"error": "focus_app requires `app`"})
        res = backend.focus_app(app, raise_window=bool(args.get("raise_window")))
        return _text_response(res)

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
        coord = args.get("coordinate") or (None, None)
        x = coord[0] if coord and len(coord) >= 1 else None
        y = coord[1] if coord and len(coord) >= 2 else None
        res = backend.click(
            x=x, y=y, button=button or "left", click_count=click_count,
            modifiers=args.get("modifiers"),
        )
        return _text_response(res)

    if action == "drag":
        res = backend.drag(
            from_xy=tuple(args["from_coordinate"]) if args.get("from_coordinate") else None,
            to_xy=tuple(args["to_coordinate"]) if args.get("to_coordinate") else None,
            button=args.get("button", "left"),
            modifiers=args.get("modifiers"),
        )
        return _text_response(res)

    if action == "scroll":
        coord = args.get("coordinate") or (None, None)
        res = backend.scroll(
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
