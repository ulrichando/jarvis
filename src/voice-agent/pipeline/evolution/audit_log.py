"""Append-only JSONL audit log for every rule-state transition.

Never raises. Caller is on a background path; an audit-log write
failure must never bubble into the user-facing turn.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any


__all__ = ["LOG_PATH", "append_event"]


logger = logging.getLogger("jarvis.evolution.audit")

LOG_PATH: Path = Path.home() / ".jarvis" / "evolution_log.jsonl"
_ALLOW_MKDIR: bool = True


def append_event(**fields: Any) -> None:
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        **fields,
    }
    try:
        if _ALLOW_MKDIR:
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.debug(f"[audit] write failed (swallowed): {e}")
