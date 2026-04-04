"""Paste store: content-addressable cache for pasted text content."""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

PASTE_STORE_DIR = "paste-cache"


def _get_paste_store_dir() -> str:
    """Get the paste store directory path."""
    config_home = os.environ.get("JARVIS_HOME", os.path.expanduser("~/.jarvis"))
    return os.path.join(config_home, PASTE_STORE_DIR)


def hash_pasted_text(content: str) -> str:
    """Generate a SHA-256 hash (first 16 hex chars) for paste content."""
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def _get_paste_path(hash_: str) -> str:
    """Get the file path for a paste by its content hash."""
    return os.path.join(_get_paste_store_dir(), f"{hash_}.txt")


async def store_pasted_text(hash_: str, content: str) -> None:
    """Store pasted text content to disk. Content-addressable: same hash = safe overwrite."""
    try:
        store_dir = _get_paste_store_dir()
        os.makedirs(store_dir, exist_ok=True)
        paste_path = _get_paste_path(hash_)
        with open(paste_path, "w", encoding="utf-8") as f:
            f.write(content)
        os.chmod(paste_path, 0o600)
        logger.debug(f"Stored paste {hash_} to {paste_path}")
    except Exception as e:
        logger.debug(f"Failed to store paste: {e}")


async def retrieve_pasted_text(hash_: str) -> Optional[str]:
    """Retrieve pasted text content by its hash. Returns None if not found."""
    try:
        paste_path = _get_paste_path(hash_)
        with open(paste_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return None
    except Exception as e:
        logger.debug(f"Failed to retrieve paste {hash_}: {e}")
        return None


async def cleanup_old_pastes(cutoff_date: datetime) -> None:
    """Clean up old paste files older than cutoff_date."""
    paste_dir = _get_paste_store_dir()

    try:
        files = os.listdir(paste_dir)
    except OSError:
        return

    cutoff_time = cutoff_date.timestamp()
    for file in files:
        if not file.endswith(".txt"):
            continue

        file_path = os.path.join(paste_dir, file)
        try:
            stat = os.stat(file_path)
            if stat.st_mtime < cutoff_time:
                os.unlink(file_path)
                logger.debug(f"Cleaned up old paste: {file_path}")
        except OSError:
            pass
