"""Cross-platform desktop control: keystrokes, typed text, window enumeration.

Sites that need to send a keystroke or focus a window (jarvis_agent.py's
``type_in_terminal``, future GUI-shortcut helpers) go through this module so
the SAME tool-surface works on Linux and Windows.

Backends
--------
Linux
    ``xdotool`` via :mod:`subprocess`. Existing JARVIS deployment target;
    behavior preserved verbatim from the pre-Phase-3.2 inline call sites in
    ``jarvis_agent.py``.

Windows
    ``pywinauto`` (Win32 automation). ``pywinauto.keyboard.send_keys`` for
    keystrokes / typed text, ``pywinauto.findwindows.find_windows`` for
    enumeration, ``pywinauto.application.Application().connect(handle=...)``
    for window activation / minimize / restore. Lazy-imported INSIDE each
    function so this module is import-safe on Linux dev boxes where
    pywinauto isn't installed (it's declared platform-conditional in
    ``requirements.txt``).

Contract
--------
* **No exception ever propagates into the caller's tool flow.** Every public
  helper catches at module boundary and returns the sentinel: ``False`` for
  bool returns, ``None`` for "find" returns, ``(False, msg)`` for the
  lower-level ``xdotool_call``. The voice loop's audio path is hot; a stray
  ``FileNotFoundError`` from a missing ``xdotool`` binary or
  ``ModuleNotFoundError`` from absent ``pywinauto`` would otherwise crash
  whatever turn called us.
* **Linux behavior is unchanged.** The Linux backend uses the same argv +
  ``--clearmodifiers`` flags + ``--sync`` semantics the inline xdotool calls
  used; the test suite covers ``type_in_terminal`` end-to-end via mock so a
  drift would be caught.
* **Keystroke syntax is the *xdotool* syntax** at the public surface
  (``"super+l"``, ``"Return"``, ``"ctrl+c"``). The Windows backend translates
  to pywinauto's ``{LWIN down}l{LWIN up}``, ``{ENTER}``, ``^c`` etc. via
  :func:`_xdotool_to_pywinauto_keys` so callers don't have to know the
  destination platform.

Public surface
--------------
* :func:`send_keys` — fire a keystroke combo (``"ctrl+c"``, ``"Return"``)
* :func:`type_text` — type literal text characters
* :func:`find_window_by_name` — substring match → window ID / HWND
* :func:`activate_window` — focus / raise a window by ID / HWND
* :func:`minimize_window`
* :func:`restore_window`
* :func:`xdotool_call` — Linux-only escape hatch for callers that want raw
  xdotool args. Returns ``(False, message)`` on non-Linux.
"""
from __future__ import annotations

import logging
import platform
import re
import subprocess
from typing import List, Optional, Tuple

__all__ = [
    "send_keys",
    "type_text",
    "find_window_by_name",
    "activate_window",
    "minimize_window",
    "restore_window",
    "xdotool_call",
]


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Platform dispatch
# ---------------------------------------------------------------------------


def _is_windows() -> bool:
    """Late-binding platform check so tests can monkeypatch platform.system."""
    return platform.system() == "Windows"


def _is_linux() -> bool:
    return platform.system() == "Linux"


# Default subprocess timeout for any xdotool call (seconds). Short — these
# are interactive primitives; if xdotool hangs longer than this the focus
# race almost certainly already lost.
_XDOTOOL_TIMEOUT = 5.0


# ---------------------------------------------------------------------------
# xdotool → pywinauto key-name translation
# ---------------------------------------------------------------------------
#
# pywinauto's ``keyboard.send_keys`` syntax (inspired by SendKeys.exe):
#   ``^`` = Ctrl  ``%`` = Alt  ``+`` = Shift  ``{LWIN}`` = Win
#   ``{ENTER}`` / ``{TAB}`` / ``{ESC}`` / ``{F1}`` ... — special keys in braces
#   To hold-and-release: ``{LWIN down}l{LWIN up}`` (Win+L), ``+{TAB}`` (Shift+Tab)
#
# xdotool's syntax:
#   space-separated key combos joined with ``+``: ``ctrl+c``, ``super+l``,
#   ``Return``, ``shift+Tab``.
#
# This module's public API takes the *xdotool* syntax (the project's existing
# convention) and the Windows backend translates. Keep the table in sync
# with what the existing call sites actually emit.

# Single-key xdotool names → pywinauto special-key tokens (in braces).
# Keys NOT in this table fall through unchanged (single chars / digits go
# verbatim; multi-char unknowns get wrapped in braces as a best-effort).
_XDOTOOL_TO_PYWINAUTO_KEY = {
    "Return":     "{ENTER}",
    "Enter":      "{ENTER}",
    "Tab":        "{TAB}",
    "Escape":     "{ESC}",
    "Esc":        "{ESC}",
    "BackSpace":  "{BACKSPACE}",
    "Backspace":  "{BACKSPACE}",
    "Delete":     "{DELETE}",
    "Del":        "{DELETE}",
    "Up":         "{UP}",
    "Down":       "{DOWN}",
    "Left":       "{LEFT}",
    "Right":      "{RIGHT}",
    "Home":       "{HOME}",
    "End":        "{END}",
    "Prior":      "{PGUP}",   # xdotool name for Page Up
    "Next":       "{PGDN}",   # xdotool name for Page Down
    "PageUp":     "{PGUP}",
    "PageDown":   "{PGDN}",
    "Insert":     "{INS}",
    "space":      " ",
    "Space":      " ",
}

# Function keys F1..F24 → ``{F1}`` .. ``{F24}``. Built programmatically so
# we don't need 24 explicit entries.
for _n in range(1, 25):
    _XDOTOOL_TO_PYWINAUTO_KEY[f"F{_n}"] = f"{{F{_n}}}"

# Modifier names xdotool emits → (prefix-char, brace-token).
# prefix-char is the shortcut form pywinauto accepts inline (e.g. ``^c``);
# brace-token is the explicit press/release form used for sticky-modifier
# combos that the prefix can't express (Win key on its own — pywinauto
# has no single-char shortcut for it).
_MODIFIER_PREFIX = {
    "ctrl":    "^",
    "control": "^",
    "alt":     "%",
    "option":  "%",
    "shift":   "+",
}

# Modifiers that pywinauto doesn't have a prefix shortcut for — emit as
# down/up bracket pairs around the body.
_MODIFIER_BRACKET = {
    "super": "LWIN",
    "win":   "LWIN",
    "meta":  "LWIN",
    "cmd":   "LWIN",
    "command": "LWIN",
}


def _xdotool_to_pywinauto_keys(combo: str) -> str:
    """Translate an xdotool key-combo string to pywinauto.send_keys syntax.

    Examples (xdotool → pywinauto):
        ``"Return"``         → ``"{ENTER}"``
        ``"ctrl+c"``         → ``"^c"``
        ``"shift+Tab"``      → ``"+{TAB}"``
        ``"alt+F4"``         → ``"%{F4}"``
        ``"ctrl+shift+t"``   → ``"^+t"``
        ``"super+l"``        → ``"{LWIN down}l{LWIN up}"``
        ``"ctrl+alt+Delete"``→ ``"^%{DELETE}"``

    The mapping deliberately favors EXPLICIT down/up brackets for any
    modifier in :data:`_MODIFIER_BRACKET` so the resulting string is
    unambiguous to pywinauto even when chained with other modifiers.
    """
    parts = [p for p in re.split(r"\s*\+\s*", combo) if p.strip()]
    if not parts:
        return ""

    body_tokens: list[str] = []
    prefix_mods: list[str] = []
    bracket_mods: list[str] = []  # pywinauto names (e.g. "LWIN")

    for part in parts:
        lower = part.strip().lower()
        if lower in _MODIFIER_PREFIX:
            prefix_mods.append(_MODIFIER_PREFIX[lower])
            continue
        if lower in _MODIFIER_BRACKET:
            bracket_mods.append(_MODIFIER_BRACKET[lower])
            continue
        # Body token — translate via the key table, else pass through.
        if part in _XDOTOOL_TO_PYWINAUTO_KEY:
            body_tokens.append(_XDOTOOL_TO_PYWINAUTO_KEY[part])
        elif lower in _XDOTOOL_TO_PYWINAUTO_KEY:
            body_tokens.append(_XDOTOOL_TO_PYWINAUTO_KEY[lower])
        elif len(part) == 1:
            # Single char (letter, digit, punct) — pywinauto takes these verbatim.
            body_tokens.append(part)
        elif re.fullmatch(r"[Ff]\d{1,2}", part):
            body_tokens.append("{" + part.upper() + "}")
        else:
            # Unknown multi-char keysym — best-effort: wrap in braces. pywinauto
            # rejects unknown brace tokens with a clear error, which the caller
            # will surface as False (we catch).
            body_tokens.append("{" + part + "}")

    body = "".join(body_tokens)
    # Prefix modifiers (Ctrl/Alt/Shift) come BEFORE the body, no braces.
    prefix = "".join(prefix_mods)

    if bracket_mods:
        # Build ``{LWIN down}<prefix><body>{LWIN up}`` (also handles multiple
        # bracket modifiers, releasing in reverse order to match the
        # press/release convention).
        downs = "".join(f"{{{m} down}}" for m in bracket_mods)
        ups = "".join(f"{{{m} up}}" for m in reversed(bracket_mods))
        return f"{downs}{prefix}{body}{ups}"
    return f"{prefix}{body}"


# ---------------------------------------------------------------------------
# Linux backend (xdotool subprocess)
# ---------------------------------------------------------------------------


def _run_xdotool(args: List[str]) -> Tuple[int, str, str]:
    """Run ``xdotool <args>``, return (rc, stdout, stderr). Never raises.

    This is the ONE allowed direct xdotool shellout in the voice-agent
    tree — every other site goes through the public surface above, which
    dispatches to pywinauto on Windows. The footgun-checker rule for
    ``xdotool subprocess invocation`` is suppressed here intentionally
    because this IS the Linux backend.
    """
    try:
        proc = subprocess.run(
            ["xdotool", *args],  # windows-footgun: ok — Linux backend of platform-dispatched helper
            capture_output=True,
            text=True,
            timeout=_XDOTOOL_TIMEOUT,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except FileNotFoundError:
        return 127, "", "xdotool not installed"
    except subprocess.TimeoutExpired:
        return 124, "", f"xdotool timed out after {_XDOTOOL_TIMEOUT}s"
    except Exception as exc:  # noqa: BLE001 — sentinel-return is the contract
        return -1, "", str(exc)


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def send_keys(keys: str) -> bool:
    """Send a keystroke combo (e.g. ``"Return"`` / ``"ctrl+c"`` / ``"super+l"``).

    Syntax is xdotool's: ``+``-joined parts, single-key names match xdotool's
    keysym table (``Return``, ``Tab``, ``Escape``, ``F1``..``F12``,
    ``Up``/``Down``/``Left``/``Right``, modifiers ``ctrl`` / ``alt`` / ``shift``
    / ``super``).

    Linux backend: ``xdotool key --clearmodifiers <keys>``.
    Windows backend: ``pywinauto.keyboard.send_keys(<translated>)`` where
    translation is via :func:`_xdotool_to_pywinauto_keys`.

    Returns:
        True on success, False on ANY failure (binary missing, parse error,
        timeout, X11 unavailable, pywinauto absent, etc.). Never raises.
    """
    if not keys or not isinstance(keys, str):
        return False

    if _is_windows():
        try:
            from pywinauto import keyboard  # type: ignore[import-not-found]
        except Exception as exc:  # noqa: BLE001 — pywinauto absent / broken
            logger.debug("send_keys: pywinauto import failed (%s)", exc)
            return False
        translated = _xdotool_to_pywinauto_keys(keys)
        if not translated:
            return False
        try:
            keyboard.send_keys(translated)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.debug("send_keys(%r → %r) failed on Windows: %s", keys, translated, exc)
            return False

    # Linux (or anything else — xdotool will simply not be present on macOS).
    rc, _out, err = _run_xdotool(["key", "--clearmodifiers", keys])
    if rc != 0:
        logger.debug("send_keys(%r) xdotool rc=%d err=%s", keys, rc, err.strip())
        return False
    return True


def type_text(text: str) -> bool:
    """Type *literal* text characters into the focused window.

    No special-key parsing — every char is emitted as-is (so ``"+"`` is a
    plus sign, not a shift modifier).

    Linux backend: ``xdotool type --clearmodifiers -- <text>``.
    Windows backend: ``pywinauto.keyboard.send_keys(<text>, with_spaces=True)``
    with explicit escaping so pywinauto's modifier characters (``^%+~()``)
    are treated as literals.

    Returns True on success, False on any error.
    """
    if not isinstance(text, str):
        return False
    if not text:
        # Typing nothing is a successful no-op.
        return True

    if _is_windows():
        try:
            from pywinauto import keyboard  # type: ignore[import-not-found]
        except Exception as exc:  # noqa: BLE001
            logger.debug("type_text: pywinauto import failed (%s)", exc)
            return False
        # Escape pywinauto's metacharacters so the text is typed literally.
        # Brackets, +, ^, %, ~, () each have to be wrapped in {} to send
        # the actual character per pywinauto docs.
        escaped = re.sub(r"([{}+^%~()])", r"{\1}", text)
        try:
            keyboard.send_keys(escaped, with_spaces=True)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.debug("type_text failed on Windows: %s", exc)
            return False

    # Linux. ``--`` terminates xdotool's option parsing so a leading ``-``
    # in the typed text isn't mistaken for a flag.
    rc, _out, err = _run_xdotool(["type", "--clearmodifiers", "--", text])
    if rc != 0:
        logger.debug("type_text xdotool rc=%d err=%s", rc, err.strip())
        return False
    return True


def find_window_by_name(pattern: str) -> Optional[int]:
    """Find a window whose title matches the substring *pattern*.

    Linux backend: ``xdotool search --name <pattern>``. Returns the LAST
    matched window ID (most-recently-mapped, matching the existing
    ``type_in_terminal`` convention).

    Windows backend: ``pywinauto.findwindows.find_windows(title_re=...)``.
    *pattern* is converted to a regex by ``re.escape``-ing then surrounding
    with ``.*`` so it remains a substring match (pywinauto's ``title_re`` is
    fullmatch by default).

    Returns the window ID / HWND as an int, or None on no match / any error.
    """
    if not pattern or not isinstance(pattern, str):
        return None

    if _is_windows():
        try:
            from pywinauto import findwindows  # type: ignore[import-not-found]
        except Exception as exc:  # noqa: BLE001
            logger.debug("find_window_by_name: pywinauto import failed (%s)", exc)
            return None
        try:
            title_re = ".*" + re.escape(pattern) + ".*"
            handles = findwindows.find_windows(title_re=title_re)
            if not handles:
                return None
            # Mirror xdotool's "last match" semantics so callers behave
            # consistently across platforms.
            return int(handles[-1])
        except Exception as exc:  # noqa: BLE001 — covers WindowNotFoundError
            logger.debug("find_window_by_name(%r) failed: %s", pattern, exc)
            return None

    # Linux.
    rc, out, err = _run_xdotool(["search", "--name", pattern])
    if rc != 0:
        logger.debug("find_window_by_name xdotool rc=%d err=%s", rc, err.strip())
        return None
    ids = [s.strip() for s in out.split() if s.strip()]
    if not ids:
        return None
    try:
        return int(ids[-1])
    except ValueError:
        return None


def activate_window(window_id: int) -> bool:
    """Bring a window to the foreground / focus.

    Linux backend: ``xdotool windowactivate --sync <id>``. ``--sync`` blocks
    until the WM grants focus, preventing the race where the caller types
    into the wrong window.

    Windows backend: ``Application().connect(handle=<id>).top_window().set_focus()``.

    Returns True on success, False on any error.
    """
    if not isinstance(window_id, int) or window_id <= 0:
        return False

    if _is_windows():
        try:
            from pywinauto.application import Application  # type: ignore[import-not-found]
        except Exception as exc:  # noqa: BLE001
            logger.debug("activate_window: pywinauto import failed (%s)", exc)
            return False
        try:
            app = Application().connect(handle=window_id)
            app.top_window().set_focus()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.debug("activate_window(%d) failed: %s", window_id, exc)
            return False

    # Linux.
    rc, _out, err = _run_xdotool(["windowactivate", "--sync", str(window_id)])
    if rc != 0:
        logger.debug("activate_window(%d) xdotool rc=%d err=%s", window_id, rc, err.strip())
        return False
    return True


def minimize_window(window_id: int) -> bool:
    """Minimize a window to the taskbar / panel.

    Linux backend: ``xdotool windowminimize <id>``.
    Windows backend: ``Application().connect(handle=<id>).top_window().minimize()``.
    """
    if not isinstance(window_id, int) or window_id <= 0:
        return False

    if _is_windows():
        try:
            from pywinauto.application import Application  # type: ignore[import-not-found]
        except Exception as exc:  # noqa: BLE001
            logger.debug("minimize_window: pywinauto import failed (%s)", exc)
            return False
        try:
            app = Application().connect(handle=window_id)
            app.top_window().minimize()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.debug("minimize_window(%d) failed: %s", window_id, exc)
            return False

    rc, _out, err = _run_xdotool(["windowminimize", str(window_id)])
    if rc != 0:
        logger.debug("minimize_window(%d) xdotool rc=%d err=%s", window_id, rc, err.strip())
        return False
    return True


def restore_window(window_id: int) -> bool:
    """Restore a minimized window (un-minimize + raise).

    Linux backend: ``xdotool windowactivate <id>`` (X11 has no dedicated
    ``windowrestore``; activate un-minimizes and raises in one step).
    Windows backend: ``Application().connect(handle=<id>).top_window().restore()``.
    """
    if not isinstance(window_id, int) or window_id <= 0:
        return False

    if _is_windows():
        try:
            from pywinauto.application import Application  # type: ignore[import-not-found]
        except Exception as exc:  # noqa: BLE001
            logger.debug("restore_window: pywinauto import failed (%s)", exc)
            return False
        try:
            app = Application().connect(handle=window_id)
            app.top_window().restore()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.debug("restore_window(%d) failed: %s", window_id, exc)
            return False

    rc, _out, err = _run_xdotool(["windowactivate", str(window_id)])
    if rc != 0:
        logger.debug("restore_window(%d) xdotool rc=%d err=%s", window_id, rc, err.strip())
        return False
    return True


def xdotool_call(args: List[str]) -> Tuple[bool, str]:
    """Lower-level escape hatch: run ``xdotool <args>`` directly.

    Use this only when a caller genuinely needs an xdotool-specific flag /
    command the high-level helpers don't expose (e.g. ``windowmove`` /
    ``getactivewindow`` / multi-key keydown sequences). Callers MUST handle
    the ``(False, _)`` return on non-Linux — there is no pywinauto
    equivalent for arbitrary xdotool arg strings.

    Returns:
        (True, stdout) on Linux when ``xdotool`` exits 0.
        (False, error_message) on non-Linux, missing binary, non-zero exit,
        or any other failure.
    """
    if _is_windows():
        return (False, "xdotool not available on Windows — use the high-level helpers")
    if not _is_linux():
        # macOS or anything else — be conservative.
        return (False, f"xdotool not available on {platform.system()}")
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        return (False, "xdotool_call requires a list[str] of args")
    rc, out, err = _run_xdotool(args)
    if rc != 0:
        return (False, err.strip() or f"xdotool exited {rc}")
    return (True, out)
