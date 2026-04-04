"""JARVIS color theme system.

Manages the reactor/UI accent color. Stored in ~/.jarvis/desktop.json
under the "theme" key. Both the tray icon and frontend read from here.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Tuple

DESKTOP_CONFIG = os.path.expanduser("~/.jarvis/desktop.json")

# ── Preset themes ──
# Each preset: (primary_hex, glow_hex, name)
# primary = main accent, glow = brighter variant for highlights
PRESETS = {
    "arc-reactor":  ("#00b8d4", "#00e5ff", "Arc Reactor (Cyan)"),
    "iron-man":     ("#ff8800", "#ffcc55", "Iron Man (Gold)"),
    "ultron":       ("#ff3333", "#ff6666", "Ultron (Red)"),
    "stealth":      ("#8b5cf6", "#a78bfa", "Stealth (Purple)"),
    "emerald":      ("#10b981", "#34d399", "Emerald (Green)"),
    "frost":        ("#38bdf8", "#7dd3fc", "Frost (Blue)"),
    "solar":        ("#f59e0b", "#fbbf24", "Solar (Amber)"),
    "hotrod":       ("#ef4444", "#f87171", "Hot Rod (Red)"),
    "ghost":        ("#94a3b8", "#cbd5e1", "Ghost (Silver)"),
}

DEFAULT_THEME = "arc-reactor"


def _load_config() -> dict:
    try:
        if os.path.exists(DESKTOP_CONFIG):
            return json.loads(Path(DESKTOP_CONFIG).read_text())
    except Exception:
        pass
    return {}


def _save_config(config: dict) -> None:
    try:
        os.makedirs(os.path.dirname(DESKTOP_CONFIG), exist_ok=True)
        Path(DESKTOP_CONFIG).write_text(json.dumps(config, indent=2))
    except Exception:
        pass


def get_theme() -> str:
    """Return current theme name."""
    cfg = _load_config()
    return cfg.get("theme", DEFAULT_THEME)


def get_colors() -> Tuple[str, str]:
    """Return (primary_hex, glow_hex) for the current theme."""
    cfg = _load_config()
    theme = cfg.get("theme", DEFAULT_THEME)

    # Custom color override
    if theme == "custom":
        primary = cfg.get("theme_primary", "#00b8d4")
        glow = cfg.get("theme_glow", "#00e5ff")
        return primary, glow

    if theme in PRESETS:
        return PRESETS[theme][0], PRESETS[theme][1]

    return PRESETS[DEFAULT_THEME][0], PRESETS[DEFAULT_THEME][1]


def set_theme(theme: str) -> Tuple[str, str]:
    """Set theme by preset name. Returns the new (primary, glow) colors."""
    cfg = _load_config()
    cfg["theme"] = theme
    _save_config(cfg)
    return get_colors()


def set_custom_color(primary: str, glow: str | None = None) -> Tuple[str, str]:
    """Set a custom hex color. If glow not given, auto-brighten."""
    if not glow:
        glow = _brighten(primary)
    cfg = _load_config()
    cfg["theme"] = "custom"
    cfg["theme_primary"] = primary
    cfg["theme_glow"] = glow
    _save_config(cfg)
    return primary, glow


def hex_to_rgb(h: str) -> Tuple[int, int, int]:
    """Convert '#rrggbb' to (r, g, b)."""
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _brighten(hex_color: str, factor: float = 1.4) -> str:
    """Brighten a hex color."""
    r, g, b = hex_to_rgb(hex_color)
    r = min(255, int(r * factor))
    g = min(255, int(g * factor))
    b = min(255, int(b * factor))
    return f"#{r:02x}{g:02x}{b:02x}"


def generate_icon(primary: str | None = None, size: int = 48) -> str:
    """Generate a tray icon PNG with the given color. Returns the file path."""
    from PIL import Image, ImageDraw

    if primary is None:
        primary, _ = get_colors()

    r, g, b = hex_to_rgb(primary)
    glow = _brighten(primary)
    gr, gg, gb = hex_to_rgb(glow)

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Outer glow ring
    draw.ellipse([2, 2, size - 3, size - 3], outline=(r, g, b, 140), width=2)
    # Inner ring
    draw.ellipse([8, 8, size - 9, size - 9], outline=(r, g, b, 200), width=2)
    # Core circle
    draw.ellipse([16, 16, size - 17, size - 17], fill=(gr, gg, gb, 255))
    # Center bright dot
    draw.ellipse([20, 20, size - 21, size - 21], fill=(255, 255, 255, 230))

    icon_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "jarvis-icon-48.png"
    )
    img.save(icon_path)
    return icon_path
