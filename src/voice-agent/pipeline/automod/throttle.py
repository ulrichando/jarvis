"""Throttle + governance gate for auto-mod intents.

Redesigned 2026-06-27 (docs/superpowers/specs/2026-06-27-evolution-governance-
redesign-design.md): the per-day build *count* cap is replaced by a cost budget
plus idle/cooldown gates. admit_intent(intent) -> (admit, reason). Gates, in order:

  1. content sanity  — non-empty intent string
  2. path blocklist  — proposed_paths_hint must avoid blocked paths
  3. IDLE            — no voice turn in the last JARVIS_EVOLUTION_IDLE_MIN (10) min
  4. BUDGET          — today's cost (cost_ledger) < JARVIS_EVOLUTION_DAILY_USD (6) — the brake
  5. COOLDOWN        — >= JARVIS_EVOLUTION_COOLDOWN_MIN (60) min since the last build
  6. count backstop  — only if JARVIS_AUTOMOD_DAILY_CAP is explicitly set (emergency)

The "signal" is implicit: an intent only reaches the gate because a detector
(patterns.py / error log / introspection / explicit user request) queued it. The
per-topic in-flight cap (1) is enforced by the spawner lockfile, not here.

mark_admitted(id) bumps the daily counter AND stamps last_build_ts (the cooldown
anchor). State persists to ~/.jarvis/auto-mods/throttle.json with date reset.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from pathlib import Path

from pipeline.automod import cost_ledger
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


def _idle_minutes() -> int:
    try:
        return max(0, int(os.environ.get("JARVIS_EVOLUTION_IDLE_MIN", "10")))
    except ValueError:
        return 10


def _cooldown_minutes() -> int:
    try:
        return max(0, int(os.environ.get("JARVIS_EVOLUTION_COOLDOWN_MIN", "60")))
    except ValueError:
        return 60


def _telemetry_db() -> Path:
    # Same resolution as pipeline.turn_telemetry.DEFAULT_DB_PATH, so the suite's
    # conftest hermeticity (JARVIS_TELEMETRY_PATH -> tmp) makes the idle gate
    # deterministic in tests while production reads the real telemetry db.
    return Path(os.environ.get(
        "JARVIS_TELEMETRY_PATH",
        str(Path.home() / ".local" / "share" / "jarvis" / "turn_telemetry.db"),
    ))


def _idle_seconds() -> float:
    """Seconds since the last voice turn. Large (=idle) if no turns / db absent."""
    db = _telemetry_db()
    if not db.exists():
        return 1e9
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            row = con.execute(
                "SELECT (julianday('now') - julianday(MAX(ts_utc))) * 86400 FROM turns"
            ).fetchone()
        finally:
            con.close()
        return float(row[0]) if row and row[0] is not None else 1e9
    except Exception:  # noqa: BLE001 — a telemetry read error must not block evolution forever
        return 1e9


def _budget_spent() -> float:
    return cost_ledger.spent_today()


def _read_state() -> dict:
    p = throttle_state_path()
    if not p.exists():
        return {"date": _today(), "admitted_today": 0}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"date": _today(), "admitted_today": 0}
    if data.get("date") != _today():
        # New day — reset the count, but keep last_build_ts so cooldown still
        # spans midnight (a build at 23:50 still cools down past 00:00).
        return {"date": _today(), "admitted_today": 0,
                **({"last_build_ts": data["last_build_ts"]} if data.get("last_build_ts") else {})}
    return data


def read_state() -> dict:
    """Return today's throttle state with date rollover applied."""
    return dict(_read_state())


def admitted_today() -> int:
    return int(_read_state().get("admitted_today", 0) or 0)


def remaining_today() -> int:
    return max(0, daily_cap() - admitted_today())


def _since_last_build_min() -> float:
    ts = _read_state().get("last_build_ts")
    if not ts:
        return 1e9
    try:
        return (time.time() - float(ts)) / 60.0
    except (TypeError, ValueError):
        return 1e9


def _write_state(state: dict) -> None:
    p = throttle_state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state), encoding="utf-8")


def admit_intent(intent: dict) -> tuple[bool, str]:
    """Returns (True, '') if the intent passes every governance gate; (False,
    reason) otherwise. Does NOT mutate state — caller calls mark_admitted()."""
    text = (intent.get("intent") or "").strip()
    if not text:
        return False, "empty_intent"

    for path in (intent.get("proposed_paths_hint") or []):
        if is_blocked_path(path):
            return False, f"blocked_path:{path}"

    if _idle_seconds() < _idle_minutes() * 60:
        return False, "not_idle"

    if _budget_spent() >= cost_ledger.daily_usd():
        return False, "budget_exhausted"

    if _since_last_build_min() < _cooldown_minutes():
        return False, "cooldown"

    # Emergency count backstop: only when JARVIS_AUTOMOD_DAILY_CAP is explicitly set.
    if os.environ.get("JARVIS_AUTOMOD_DAILY_CAP") and admitted_today() >= daily_cap():
        return False, "daily_cap_reached"

    return True, ""


def mark_admitted(intent_id: str) -> None:
    state = _read_state()
    state["admitted_today"] = state.get("admitted_today", 0) + 1
    state["last_build_ts"] = time.time()
    _write_state(state)
    logger.info("[automod] admitted: id=%s spent_today=$%.2f/%.2f (cooldown anchor set)",
                intent_id, _budget_spent(), cost_ledger.daily_usd())
