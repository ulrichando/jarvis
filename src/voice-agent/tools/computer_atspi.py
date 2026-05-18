"""AT-SPI widget enumeration — grounding side-channel for computer-use.

Spec: docs/superpowers/specs/2026-05-18-jarvis-computer-use-parity-design.md §4

Returns a flat list of currently-visible interactive widgets from the
active window, with bounds/role/text — used to ground the LLM's clicks
on apps with good a11y trees (most GTK/Qt). Returns [] when AT-SPI
is sparse (canvas apps, games, Electron without a11y) — caller falls
back to bare vision.

Cached for 100ms within one iteration step to avoid hammering D-Bus
on multiple lookups during a single loop iteration.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional


logger = logging.getLogger("jarvis.computer_atspi")


__all__ = ["Widget", "enumerate_widgets"]


@dataclass
class Widget:
    role: str               # "push_button" | "text" | "menu_item" | "password_text" | ...
    bounds: tuple[int, int, int, int]  # (x, y, w, h) in native screen coords
    text: str               # label / value / name
    enabled: bool
    active: bool            # has focus


# Module-level cache. enumerate_widgets() with the same window filter
# returns the cached result within _CACHE_TTL_S of the last lookup.
_CACHE_KEY: Optional[str] = None
_CACHE_VAL: list[Widget] = []
_CACHE_TS: float = 0.0
_CACHE_TTL_S: float = 0.1   # 100 ms — matches §4 spec


def _get_desktop():
    """Return the pyatspi desktop root, or raise on failure.

    Isolated function so tests can monkeypatch without importing
    pyatspi at the top of the module (pyatspi is unavailable in CI
    runners without a D-Bus session)."""
    import pyatspi
    return pyatspi.Registry.getDesktop(0)


def _enumerate_descendants(root) -> list:
    """Walk all descendants of `root`. Returns a flat list of
    accessibles. Stops at depth 12 to avoid runaway on bad trees."""
    out: list = []
    stack = [(root, 0)]
    while stack:
        node, depth = stack.pop()
        if depth > 12:
            continue
        try:
            n = node.childCount
        except Exception:
            continue
        for i in range(n):
            try:
                child = node.getChildAtIndex(i)
            except Exception:
                continue
            if child is None:
                continue
            out.append(child)
            stack.append((child, depth + 1))
    return out


def _accessible_to_widget(acc) -> Optional[Widget]:
    """Convert one pyatspi accessible into a Widget, or None if the
    accessible isn't interactive / has no bounds / is invisible."""
    try:
        role = acc.getRoleName()
    except Exception:
        return None
    # Only keep widgets that are likely interactive or readable.
    interesting_roles = {
        "push_button", "toggle_button", "radio_button", "check_box",
        "menu_item", "menu", "tab", "tab_list",
        "text", "entry", "password_text", "combo_box",
        "list_item", "tree_item", "link",
        "slider", "spin_button", "scroll_bar",
    }
    if role not in interesting_roles:
        return None
    try:
        comp = acc.queryComponent()
    except Exception:
        return None
    try:
        extents = comp.getExtents(0)  # COORD_TYPE_SCREEN = 0
    except Exception:
        return None
    if extents.width <= 0 or extents.height <= 0:
        return None
    try:
        name = acc.name or ""
    except Exception:
        name = ""
    try:
        state = acc.getState()
        enabled = state.contains("enabled")
        active = state.contains("active") or state.contains("focused")
    except Exception:
        enabled = True
        active = False
    return Widget(
        role=role,
        bounds=(extents.x, extents.y, extents.width, extents.height),
        text=name,
        enabled=enabled,
        active=active,
    )


def enumerate_widgets(
    window_title_pattern: str | None = None,
) -> list[Widget]:
    """Return a flat list of visible interactive widgets from the
    active window (or any window matching the title pattern).

    Returns [] silently when:
      - AT-SPI / D-Bus is unavailable (logs debug, doesn't raise)
      - The active app has no a11y tree (canvas apps, games, etc.)

    Cached for 100ms within an iteration step.
    """
    global _CACHE_KEY, _CACHE_VAL, _CACHE_TS
    key = window_title_pattern or ""
    now = time.monotonic()
    if _CACHE_KEY == key and (now - _CACHE_TS) < _CACHE_TTL_S:
        return _CACHE_VAL

    try:
        desktop = _get_desktop()
    except Exception as e:
        logger.debug(f"[computer_atspi] desktop unavailable: {e}")
        _CACHE_KEY = key
        _CACHE_VAL = []
        _CACHE_TS = now
        return []

    # Find the target frame(s). Iterate top-level apps; for each, walk
    # frames; if title matches (or no pattern), enumerate descendants.
    candidates: list = []
    try:
        for app in desktop:
            try:
                for child in (app.getChildAtIndex(i) for i in range(app.childCount)):
                    if child is None:
                        continue
                    if window_title_pattern is None:
                        candidates.append(child)
                    else:
                        try:
                            name = child.name or ""
                        except Exception:
                            name = ""
                        if window_title_pattern.lower() in name.lower():
                            candidates.append(child)
            except Exception:
                continue
    except Exception:
        candidates = []

    widgets: list[Widget] = []
    for root in candidates:
        for acc in _enumerate_descendants(root):
            w = _accessible_to_widget(acc)
            if w is not None:
                widgets.append(w)

    _CACHE_KEY = key
    _CACHE_VAL = widgets
    _CACHE_TS = now
    return widgets
