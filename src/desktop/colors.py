"""JARVIS color theme system.

Manages the reactor/UI accent color. Stored in ~/.jarvis/desktop.json
under the "theme" / "theme_primary" / "theme_glow" keys.
Both the tray icon and frontend read from here.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Tuple

DESKTOP_CONFIG = os.path.expanduser("~/.jarvis/desktop.json")

# ── Preset themes ──
# Each preset: (primary_hex, glow_hex, label)
PRESETS = {
    "cyan":   ("#00e5ff", "#0088aa", "Cyan (Classic)"),
    "blue":   ("#60a5fa", "#3b82f6", "Blue (Cool)"),
    "green":  ("#4ade80", "#16a34a", "Green (Matrix)"),
    "amber":  ("#f59e0b", "#d97706", "Amber (Warm)"),
    "red":    ("#ef4444", "#dc2626", "Red (Alert)"),
    "violet": ("#a78bfa", "#7c3aed", "Violet (Mystic)"),
    "ghost":  ("#94a3b8", "#cbd5e1", "Ghost (Silver)"),
}

DEFAULT_THEME = "ghost"


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
    """Return current theme name (reads from config)."""
    return _load_config().get("theme", DEFAULT_THEME)


def get_colors() -> Tuple[str, str]:
    """Return (primary_hex, glow_hex) for the current theme.

    Handles both preset and custom themes by reading config.
    """
    cfg = _load_config()
    theme = cfg.get("theme", DEFAULT_THEME)
    if theme == "custom":
        primary = cfg.get("theme_primary", PRESETS[DEFAULT_THEME][0])
        glow    = cfg.get("theme_glow",    _brighten(primary))
        return primary, glow
    preset = PRESETS.get(theme, PRESETS[DEFAULT_THEME])
    return preset[0], preset[1]


def set_theme(theme: str) -> Tuple[str, str]:
    """Set theme by preset name. Returns the new (primary, glow) colors."""
    cfg = _load_config()
    cfg["theme"] = theme
    # Store the resolved colors too so the desktop reads them back instantly
    if theme in PRESETS:
        cfg["theme_primary"] = PRESETS[theme][0]
        cfg["theme_glow"]    = PRESETS[theme][1]
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
    """Generate a tray icon PNG with the given color. Returns the file path.

    Uses a color-stamped filename so AppIndicator detects the change
    (it caches by path and ignores writes to the same file).
    """
    from PIL import Image, ImageDraw
    import glob as _glob

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

    # Unique filename per color so AppIndicator picks up the change
    icon_dir = os.path.dirname(os.path.abspath(__file__))
    color_tag = primary.lstrip("#")
    icon_path = os.path.join(icon_dir, f"jarvis-icon-{color_tag}.png")
    img.save(icon_path)

    # Also save as the default name (for first boot / fallback)
    default_path = os.path.join(icon_dir, "jarvis-icon-48.png")
    img.save(default_path)

    # Clean up old color-tagged icons
    for old in _glob.glob(os.path.join(icon_dir, "jarvis-icon-??????.png")):
        if old != icon_path:
            try:
                os.unlink(old)
            except OSError:
                pass

    return icon_path
