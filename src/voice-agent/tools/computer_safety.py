"""Safety gates for the computer-use loop.

Two functions, both pure / side-effect-free outside their inputs:
  - parse_destructive_intent(action, widgets) -> Optional[str]
    Returns a confirmation phrase or None.
  - is_password_field_visible(png, widgets) -> bool
    Layer 1: AT-SPI password_text role. Layer 2: Gemini fallback.

Spec: docs/superpowers/specs/2026-05-18-jarvis-computer-use-parity-design.md §4
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from tools.computer_atspi import Widget


logger = logging.getLogger("jarvis.computer_safety")


__all__ = ["parse_destructive_intent", "is_password_field_visible"]


# Words that, when present in a button label or typed command, require
# voice confirmation before the action proceeds. Case-insensitive whole-
# word match.
_DESTRUCTIVE_VERBS: set[str] = {
    "delete", "send", "submit", "overwrite", "format", "remove",
    "erase", "discard", "publish", "post", "drop", "wipe",
}

# Shell commands that should never auto-run via the type action.
_DESTRUCTIVE_SHELL_RE = re.compile(
    r"\b(?:rm\s+-rf|rm\s+-r|dd\s+if=|mkfs|format|shred|wipefs|"
    r"sudo\s+rm|sudo\s+dd|chmod\s+-R\s+000|chown\s+-R\s+0:0)\b",
    re.IGNORECASE,
)


def _widget_at(coord: tuple[int, int], widgets: list[Widget]) -> Optional[Widget]:
    """Return the widget whose bounds contain coord, or None."""
    x, y = coord
    for w in widgets:
        wx, wy, ww, wh = w.bounds
        if wx <= x < wx + ww and wy <= y < wy + wh:
            return w
    return None


def _widget_text_is_destructive(text: str) -> bool:
    """Match the destructive verb vocabulary against widget text. Case-
    insensitive, whole-word."""
    if not text:
        return False
    pat = r"\b(?:" + "|".join(_DESTRUCTIVE_VERBS) + r")\b"
    return re.search(pat, text, re.IGNORECASE) is not None


def parse_destructive_intent(
    action: dict, widgets: list[Widget]
) -> Optional[str]:
    """Return a confirmation phrase for destructive actions, or None.

    Trigger patterns:
      1. left_click whose coordinate hits a widget with destructive
         text ("Delete", "Send", "Submit", ...).
      2. type whose text matches a destructive shell pattern
         (rm -rf, dd if=, mkfs, ...).

    All other actions (screenshot, mouse_move, scroll, etc.) return
    None — they're inherently non-destructive.
    """
    if not isinstance(action, dict):
        return None
    kind = action.get("action") or action.get("name", "")
    if kind in ("left_click", "double_click", "triple_click"):
        coord = action.get("coordinate")
        if not coord or len(coord) != 2:
            return None
        w = _widget_at(tuple(coord), widgets)
        if w is None:
            return None
        if _widget_text_is_destructive(w.text):
            return (
                f"About to click '{w.text}' (a {w.role.replace('_', ' ')}). "
                f"This looks destructive — proceed?"
            )
        return None
    if kind == "type":
        text = action.get("text", "")
        if _DESTRUCTIVE_SHELL_RE.search(text):
            return (
                f"About to type a destructive shell command "
                f"({text[:60]!r}) — proceed?"
            )
        return None
    return None


async def _gemini_password_check(png: bytes) -> bool:
    """Ask Gemini Flash Lite whether the screenshot contains a focused
    password input. Lightweight model so latency overhead is ~300 ms.

    Test seam: monkeypatch this to return True/False in unit tests
    without hitting Gemini."""
    # Lazy import — keep Gemini optional. If unavailable, conservatively
    # return False so we don't false-positive everything.
    try:
        from tools._vision_backend import vision_describe
    except Exception:
        return False
    try:
        desc = await vision_describe(
            png,
            prompt=(
                "Is there a focused password input field visible on this "
                "screen? Answer with EXACTLY one word: 'yes' or 'no'."
            ),
        )
        return desc.strip().lower().startswith("yes")
    except Exception as e:
        logger.debug(f"[computer_safety] gemini password check failed: {e}")
        return False


async def is_password_field_visible(
    png: bytes, widgets: list[Widget]
) -> bool:
    """True if the screen appears to have a focused password input.

    Two-layer check:
      Layer 1: any widget with role == "password_text" (AT-SPI).
      Layer 2: Gemini Flash Lite on the screenshot — only consulted
               when AT-SPI returned no widgets at all (sparse
               accessibility tree).
    """
    for w in widgets:
        if w.role == "password_text":
            return True
    if not widgets:
        # AT-SPI is sparse — fall back to vision.
        return await _gemini_password_check(png)
    # AT-SPI returned widgets but no password_text → trust it.
    return False
