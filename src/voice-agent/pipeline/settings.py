"""Unified-settings reader.

`read_unified_setting(key, file_path)` returns the active value of a
user-tunable setting from the flat file the tray UI writes.

Pre-2026-05-22 this also consulted the hub daemon's state.db (canonical
post-2026-05-03) and fell back to the file. The hub subsystem was
removed entirely on 2026-05-22, so the file is now the only source.

Returns None if the file has no value — caller decides what the
default means.

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
    # Flat file written by the tray UI. (Hub-backed state.db lookup
    # removed 2026-05-22 alongside the rest of the hub subsystem.)
    try:
        v = file_path.read_text(encoding="utf-8").strip()
        return v if v else None
    except FileNotFoundError:
        return None
    except Exception as e:
        logger.warning(f"could not read {file_path}: {e}")
        return None
