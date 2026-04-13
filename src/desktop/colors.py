"""
Theme colour management — GTK-free shim.

The GTK overlay has been replaced by the Tauri desktop app.
This module preserves the original API so all callers (web_server,
extra.py, desktop.py) continue to work without modification.

Theme state lives in ~/.jarvis/settings.json under the key "theme".
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# ── Presets ────────────────────────────────────────────────────────────────
# (primary_hex, glow_hex, label)
PRESETS: dict[str, tuple[str, str, str]] = {
    "cyan":       ("#00e5ff", "#0088aa", "Cyan (Classic)"),
    "blue":       ("#60a5fa", "#3b82f6", "Blue (Cool)"),
    "green":      ("#4ade80", "#16a34a", "Green (Matrix)"),
    "amber":      ("#f59e0b", "#d97706", "Amber (Warm)"),
    "red":        ("#ef4444", "#dc2626", "Red (Alert)"),
    "violet":     ("#a78bfa", "#7c3aed", "Violet (Mystic)"),
    "ghost":      ("#94a3b8", "#cbd5e1", "Ghost (Silver)"),
    # Legacy aliases kept for backward compat
    "arc-reactor": ("#00e5ff", "#0088aa", "Arc Reactor"),
    "iron-man":    ("#ef4444", "#dc2626", "Iron Man"),
    "ultron":      ("#a78bfa", "#7c3aed", "Ultron"),
    "stealth":     ("#94a3b8", "#cbd5e1", "Stealth"),
    "emerald":     ("#4ade80", "#16a34a", "Emerald"),
    "frost":       ("#60a5fa", "#3b82f6", "Frost"),
    "solar":       ("#f59e0b", "#d97706", "Solar"),
    "hotrod":      ("#ef4444", "#dc2626", "Hot Rod"),
}

_SETTINGS_PATH = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis")) / "settings.json"
_DEFAULT_THEME = "ghost"
_DEFAULT_PRIMARY = "#94a3b8"
_DEFAULT_GLOW = "#cbd5e1"


def _load_settings() -> dict:
    try:
        if _SETTINGS_PATH.exists():
            return json.loads(_SETTINGS_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def _save_settings(data: dict) -> None:
    try:
        _SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _SETTINGS_PATH.write_text(json.dumps(data, indent=2))
    except OSError:
        pass


# ── Public API ─────────────────────────────────────────────────────────────

def get_theme() -> str:
    """Return the current theme name."""
    return _load_settings().get("theme", _DEFAULT_THEME)


def get_colors() -> tuple[str, str]:
    """Return (primary_hex, glow_hex) for the current theme."""
    settings = _load_settings()
    theme = settings.get("theme", _DEFAULT_THEME)
    if theme in PRESETS:
        return PRESETS[theme][0], PRESETS[theme][1]
    # Custom color stored directly
    primary = settings.get("theme_primary", _DEFAULT_PRIMARY)
    glow = settings.get("theme_glow", _DEFAULT_GLOW)
    return primary, glow


def set_theme(name: str) -> tuple[str, str]:
    """Switch to a named preset. Returns (primary, glow)."""
    if name not in PRESETS:
        raise ValueError(f"Unknown theme: {name!r}. Available: {list(PRESETS)}")
    primary, glow, _ = PRESETS[name]
    settings = _load_settings()
    settings["theme"] = name
    settings.pop("theme_primary", None)
    settings.pop("theme_glow", None)
    _save_settings(settings)
    return primary, glow


def set_custom_color(primary: str, glow: str | None = None) -> tuple[str, str]:
    """Set a custom hex colour. Derives a glow if not provided."""
    if not primary.startswith("#"):
        primary = f"#{primary}"
    if glow is None:
        # Darken primary by ~40 % as glow
        r = int(primary[1:3], 16)
        g = int(primary[3:5], 16)
        b = int(primary[5:7], 16)
        glow = "#{:02x}{:02x}{:02x}".format(
            max(0, int(r * 0.6)),
            max(0, int(g * 0.6)),
            max(0, int(b * 0.6)),
        )
    settings = _load_settings()
    settings["theme"] = "custom"
    settings["theme_primary"] = primary
    settings["theme_glow"] = glow
    _save_settings(settings)
    return primary, glow


def generate_icon(primary: str | None = None) -> None:
    """
    Regenerate the system-tray icon with the given colour.

    The Tauri app uses a compile-time embedded icon so this is a no-op at
    runtime.  The function is kept so callers don't need to be changed.
    """
    pass  # Tauri icon is embedded at build time; runtime regen not needed.
