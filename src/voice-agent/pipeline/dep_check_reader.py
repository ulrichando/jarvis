"""Read the dependency check result and provide status/summary for CLI tools.

The shell script (scripts/jarvis-dep-check.sh) writes structured JSON to
``~/.jarvis/dep-check/result.json`` and queues voice digests into the
existing cron pending queue. This module provides a Python reader for
programmatic access — the CLI tool (bin/jarvis-dep-check) imports it,
and future integrations can reuse ``get_latest_result()`` and
``get_summary()``.

Voice delivery is handled by the shell script directly (it appends to
``~/.jarvis/cron/pending.jsonl``, which the existing
``_cron_pending_watcher`` in jarvis_agent.py drains at session-connect).
No jarvis_agent.py wiring is needed.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("jarvis.dep_check_reader")

RESULT_FILE: Path = Path.home() / ".jarvis" / "dep-check" / "result.json"
LAST_VOICED_FILE: Path = Path.home() / ".jarvis" / "dep-check" / ".last_voiced"


def get_latest_result() -> Optional[dict[str, Any]]:
    """Return the latest dep-check result dict, or None if unavailable."""
    try:
        if not RESULT_FILE.exists():
            return None
        return json.loads(RESULT_FILE.read_text("utf-8"))
    except Exception:
        logger.debug("Failed to read dep-check result", exc_info=True)
        return None


def get_summary() -> str:
    """Return a one-line human-readable summary of the latest check."""
    result = get_latest_result()
    if result is None:
        return "No dependency check has run yet. Run: jarvis-dep-check check"

    status = result.get("status", "?")
    missing = result.get("missing", [])
    skew = result.get("skew", [])
    outdated = result.get("outdated", [])
    check_ts = result.get("check_ts", "?")

    parts = [f"Status: {status.upper()}"]
    if missing:
        names = ", ".join(m.get("name", "?") for m in missing)
        parts.append(f"Missing: {names}")
    if skew:
        names = ", ".join(s.get("plugin", "?") for s in skew)
        parts.append(f"Version skew: {names}")
    if outdated:
        parts.append(f"Outdated: {len(outdated)} package(s)")
    if not missing and not skew and not outdated:
        parts.append("All dependencies up to date")
    parts.append(f"(checked {check_ts})")
    return "  ".join(parts)


def is_new_since_last_voiced() -> bool:
    """True if the latest result is newer than when we last voiced findings."""
    result = get_latest_result()
    if result is None:
        return False
    check_ts_str = result.get("check_ts", "")
    try:
        check_ts = datetime.fromisoformat(check_ts_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return False
    try:
        last = float(LAST_VOICED_FILE.read_text("utf-8").strip())
        last_ts = datetime.fromtimestamp(last, tz=timezone.utc)
    except Exception:
        last_ts = datetime.min.replace(tzinfo=timezone.utc)
    return check_ts > last_ts


def mark_voiced() -> None:
    """Record that the current findings have been voiced."""
    LAST_VOICED_FILE.parent.mkdir(parents=True, exist_ok=True)
    LAST_VOICED_FILE.write_text(str(datetime.now(timezone.utc).timestamp()))


def get_findings_for_voice() -> Optional[str]:
    """Return a concise voice-digest string if findings are new, or None.

    Only returns text on ``warn`` / ``crit`` status — clean ``ok`` results
    are silently skipped. Marks the result as voiced after reading so the
    same findings aren't spoken twice.
    """
    if not is_new_since_last_voiced():
        return None
    result = get_latest_result()
    if result is None:
        return None
    status = result.get("status", "ok")
    if status == "ok":
        mark_voiced()
        return None

    missing = result.get("missing", [])
    skew = result.get("skew", [])
    outdated = result.get("outdated", [])

    parts = ["Dependency check:"]
    if missing:
        names = [m.get("name", "?") for m in missing]
        parts.append(f"{len(names)} package{'s' if len(names) != 1 else ''} missing — {', '.join(names)}.")
    if skew:
        details = [f"{s.get('plugin','?')} version {s.get('plugin_version','?')} doesn't match livekit-agents {s.get('base_version','?')}" for s in skew]
        parts.append(f"Version skew: {'; '.join(details)}.")
    if outdated:
        parts.append(f"{len(outdated)} package{'s' if len(outdated) != 1 else ''} outdated.")

    mark_voiced()
    return " ".join(parts)
