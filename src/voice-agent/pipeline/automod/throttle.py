"""Throttle + blocklist gate for auto-mod intents (Spec B, Plane 3).

admit_intent(intent) -> (admit: bool, reason: str). Three gates:
  1. content sanity (non-empty intent string after strip)
  2. path blocklist (proposed_paths_hint, if any, must not include
     blocked paths and must stay inside ALLOWED_PATH_PREFIX)
  3. daily cap (default 5 evolutions/day; configurable via JARVIS_AUTOMOD_DAILY_CAP)

Per-topic in-flight cap (1) is enforced separately by the spawner's
lockfile (B-T8), not here.

mark_admitted(id) bumps the daily counter after admission. The caller
(spawner.py) is responsible for calling it on successful admit.

State persists to ~/.jarvis/auto-mods/throttle.json with date-based
reset: a new day means counter starts at 0 again.
"""
from __future__ import annotations

import json
import logging
import os
import time

from pipeline.automod._state import (
    is_blocked_path,
    throttle_state_path,
)

logger = logging.getLogger("jarvis.automod.throttle")
DEFAULT_DAILY_CAP = 5


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def daily_cap() -> int:
    try:
        return max(1, int(os.environ.get("JARVIS_AUTOMOD_DAILY_CAP", str(DEFAULT_DAILY_CAP))))
    except ValueError:
        return DEFAULT_DAILY_CAP


def _daily_cap() -> int:
    return daily_cap()


def _read_state() -> dict:
    p = throttle_state_path()
    if not p.exists():
        return {"date": _today(), "admitted_today": 0}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"date": _today(), "admitted_today": 0}
    if data.get("date") != _today():
        # New day — reset.
        return {"date": _today(), "admitted_today": 0}
    return data


def read_state() -> dict:
    """Return today's throttle state with date rollover applied."""
    return dict(_read_state())


def admitted_today() -> int:
    return int(_read_state().get("admitted_today", 0) or 0)


def remaining_today() -> int:
    return max(0, daily_cap() - admitted_today())


def _write_state(state: dict) -> None:
    p = throttle_state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state), encoding="utf-8")


def admit_intent(intent: dict) -> tuple[bool, str]:
    """Returns (True, '') if the intent passes all gates; (False, reason)
    otherwise. Does NOT mutate state — caller must call mark_admitted()
    after the spawner takes the intent."""
    text = (intent.get("intent") or "").strip()
    if not text:
        return False, "empty_intent"

    # Path blocklist on proposed_paths_hint (if any).
    hint = intent.get("proposed_paths_hint") or []
    for path in hint:
        if is_blocked_path(path):
            return False, f"blocked_path:{path}"

    # Daily cap.
    state = _read_state()
    if state["admitted_today"] >= daily_cap():
        return False, "daily_cap_reached"

    return True, ""


def mark_admitted(intent_id: str) -> None:
    state = _read_state()
    state["admitted_today"] = state.get("admitted_today", 0) + 1
    _write_state(state)
    logger.info("[automod] admitted: id=%s today=%d/%d",
                intent_id, state["admitted_today"], daily_cap())
