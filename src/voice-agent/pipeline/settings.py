"""Unified-settings reader.

`read_unified_setting(key, file_path)` reads a user-tunable setting
from the given file path (the flat file the tray UI writes). Returns
None if the file has no value — caller decides what the default means.

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
    try:
        v = file_path.read_text(encoding="utf-8").strip()
        return v if v else None
    except FileNotFoundError:
        return None
    except Exception as e:
        logger.warning(f"could not read {file_path}: {e}")
        return None
