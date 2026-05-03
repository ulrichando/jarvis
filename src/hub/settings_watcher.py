"""Settings file watcher.

Watches three flat-text files in ~/.jarvis/ and publishes
settings.value.changed events when their content changes.

Hard blocklist: any file path whose basename contains 'keys', 'env',
'secret', 'token', or 'password' (case-insensitive) is REFUSED —
sensitive material does not flow through the hub event log.

Usage:
    state: dict[str, str] = {}      # caller-owned, persists across ticks
    while running:
        await scan_once(redis, WATCHED, state)
        await asyncio.sleep(1.0)
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger("jarvis.hub.settings_watcher")

EVENTS_STREAM = "events:settings"

# Hard blocklist — never watch these. Belt-and-suspenders against
# someone accidentally adding `keys.env` to the WATCHED mapping.
_SENSITIVE_PATTERN = re.compile(r"keys|env|secret|token|password", re.IGNORECASE)


def _read_value(path: Path) -> str | None:
    """Read the trimmed contents of a settings file. None if missing."""
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except Exception as e:
        logger.warning("[settings-watcher] failed to read %s: %s", path, e)
        return None


def _stable_event_id(key: str, value: str, mtime_ns: int) -> str:
    """Deterministic event id so identical (key, value, mtime) edits
    are deduped at the state.db UPSERT layer if they reach the watcher
    twice (e.g., daemon restart during a write race)."""
    h = hashlib.sha256(f"{key}|{value}|{mtime_ns}".encode())
    return h.hexdigest()[:32]


async def scan_once(
    redis: Any,
    watched: dict[str, Path],
    state: dict[str, str],
) -> int:
    """Walk every (key, path) in `watched`, compare current value to
    `state[key]`, publish settings.value.changed events on change.
    Returns count of events published.

    Mutates `state` in-place — caller persists it across ticks.

    Raises ValueError IMMEDIATELY (no events published) if any
    `watched` entry has a sensitive-looking name.
    """
    # Sensitivity check — fail loud BEFORE publishing anything.
    for key, path in watched.items():
        if _SENSITIVE_PATTERN.search(key) or _SENSITIVE_PATTERN.search(path.name):
            raise ValueError(
                f"refusing to watch sensitive file {path} (key={key!r}). "
                f"Sensitive material must never flow through the event log."
            )

    published = 0
    for key, path in watched.items():
        value = _read_value(path)
        if value is None:
            continue  # file missing — skip silently
        if state.get(key) == value:
            continue  # unchanged

        try:
            mtime_ns = path.stat().st_mtime_ns
        except FileNotFoundError:
            continue

        eid = _stable_event_id(key, value, mtime_ns)
        evt = {
            "source": "hub",
            "source_event_id": eid,
            "type": "settings.value.changed",
            "session_id": "system",
            "source_ts": int(mtime_ns / 1_000_000),  # ms
            "payload": {"key": key, "value": value},
        }
        try:
            await redis.xadd(EVENTS_STREAM, {"data": json.dumps(evt)})
            state[key] = value
            published += 1
            logger.info(
                "[settings-watcher] published %s = %r", key, value[:80]
            )
        except Exception:
            logger.exception(
                "[settings-watcher] xadd failed for %s; will retry next tick", key
            )

    return published
