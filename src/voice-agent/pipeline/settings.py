"""Unified-settings reader.

`read_unified_setting(key, file_path)` returns the active value of a
user-tunable setting, looking first at the hub's state.db (canonical
post-2026-05-03 — the hub daemon's settings_watcher writes here on
every change) and falling back to the flat file the tray UI still
writes for the transition window.

Returns None if neither source has a value — caller decides what the
default means. See spec
docs/superpowers/specs/2026-05-03-jarvis-unified-settings.md.

Hoisted from `jarvis_agent.py` 2026-05-10 so the LLM / TTS provider
modules can read settings without a lazy-import dance back into
jarvis_agent.
"""
from __future__ import annotations

import logging
from pathlib import Path


logger = logging.getLogger("jarvis.settings")


__all__ = ["read_unified_setting"]


def read_unified_setting(key: str, file_path: Path) -> str | None:
    # 1. State.db (canonical post-2026-05-03)
    try:
        from hub.client import HubClient as _HubClient
        v = _HubClient.read_setting_sync(key)
        if v:
            return v
    except Exception:
        pass  # SDK unavailable / state.db missing — fall through
    # 2. Flat file (legacy, still written by the tray)
    try:
        v = file_path.read_text(encoding="utf-8").strip()
        return v if v else None
    except FileNotFoundError:
        return None
    except Exception as e:
        logger.warning(f"could not read {file_path}: {e}")
        return None
