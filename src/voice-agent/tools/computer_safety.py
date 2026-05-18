"""Safety gates for the computer-use loop.

Three functions, all pure / side-effect-free outside their inputs:
  - parse_destructive_intent(action, widgets) -> Optional[str]
    Returns a confirmation phrase or None.
  - check_password_visible(png, widgets) -> tuple[bool, str]
    Bounded-latency (≤_GEMINI_TIMEOUT_S, default 1.5s) password-field
    detection. Returns (visible, state) where state is one of
    'fastpath_hit' / 'fastpath_miss' / 'slowpath' / 'failopen'.
    Fail-open on timeout by default; fail-closed when
    JARVIS_PASSWORD_CHECK_STRICT=1.
  - is_password_field_visible(png, widgets) -> bool
    Back-compat wrapper around check_password_visible.

Env vars:
  - JARVIS_PASSWORD_CHECK_TIMEOUT_S (default 1.5) — Gemini timeout.
  - JARVIS_PASSWORD_CHECK_STRICT (default unset) — '1' to fail-closed.

Spec: docs/superpowers/specs/2026-05-18-cua-password-check-failopen-design.md
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import time
from typing import Optional

from tools.computer_atspi import Widget


logger = logging.getLogger("jarvis.computer_safety")


__all__ = [
    "parse_destructive_intent",
    "is_password_field_visible",
    "check_password_visible",
]


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

# Hard timeout for the Gemini fallback in check_password_visible.
# Research-validated 2026-05-18: 1.5s preserves the 30-iter loop's
# wall-clock budget (30 × 1.5 = 45s worst case vs 30 × 10 = 300s
# without the cap). Tighter values (e.g. 0.8s) save more wall-clock
# but increase fail-open ratio on slow Gemini days. Overridable via
# env: JARVIS_PASSWORD_CHECK_TIMEOUT_S. Spec:
# docs/superpowers/specs/2026-05-18-cua-password-check-failopen-design.md
_GEMINI_TIMEOUT_S: float = float(
    os.environ.get("JARVIS_PASSWORD_CHECK_TIMEOUT_S", "1.5")
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


async def check_password_visible(
    png: bytes, widgets: list[Widget]
) -> tuple[bool, str]:
    """Two-layer password-field detection with bounded latency.

    Returns (visible, state) where state is one of:
      - "fastpath_hit":   AT-SPI saw a password_text widget. Microseconds.
      - "fastpath_miss":  AT-SPI returned widgets but none were password
                          fields. Microseconds.
      - "slowpath":       AT-SPI empty; Gemini fallback ran and answered
                          within _GEMINI_TIMEOUT_S. ~hundreds of ms in
                          the happy case.
      - "failopen":       Gemini timed out or raised. Returns False
                          (fail-open) by default, True (fail-closed)
                          when JARVIS_PASSWORD_CHECK_STRICT=1.

    Rationale: Anthropic's reference computer_use_demo/loop.py ships
    NO client-side password check — they trust model training plus a
    server-side prompt-injection classifier. JARVIS's check is
    defense-in-depth that MUST NOT dominate latency. Per the
    2026-05-18 industry-validation research and OS-Harm benchmark
    (arxiv 2506.14866), fail-open on this layer is correct because
    Sonnet 4.6's own training is the primary defense.
    """
    # Layer 1 — AT-SPI fast path (microseconds)
    for w in widgets:
        if w.role == "password_text":
            return True, "fastpath_hit"
    if widgets:
        return False, "fastpath_miss"

    # Layer 2 — Gemini fallback (bounded by _GEMINI_TIMEOUT_S)
    started = time.monotonic()
    try:
        result = await asyncio.wait_for(
            _gemini_password_check(png),
            timeout=_GEMINI_TIMEOUT_S,
        )
        return bool(result), "slowpath"
    except (asyncio.TimeoutError, Exception) as e:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        strict = os.environ.get("JARVIS_PASSWORD_CHECK_STRICT") == "1"
        shot_hash = hashlib.md5(png).hexdigest()[:12] if png else "empty"
        logger.warning(
            f"[computer_safety] password check failed open "
            f"(cause={type(e).__name__}, elapsed_ms={elapsed_ms}, "
            f"shot_hash={shot_hash}, widgets_count={len(widgets)}, "
            f"strict_mode={strict})"
        )
        return strict, "failopen"


async def is_password_field_visible(
    png: bytes, widgets: list[Widget]
) -> bool:
    """Back-compat wrapper around check_password_visible.

    Existing callers that only need the bool (no state tag) get the
    same semantics. New callers should use check_password_visible
    directly to capture the state for audit logging.

    Note: this wrapper inherits the bounded-latency behaviour of
    check_password_visible — the unbounded Gemini call that existed
    pre-2026-05-18 is gone. Callers that relied on infinite waits will
    now fail-open on Gemini timeout (or fail-closed if
    JARVIS_PASSWORD_CHECK_STRICT=1).
    """
    visible, _state = await check_password_visible(png, widgets)
    return visible
