"""Per-day cost ledger for the evolution loop — the real spend brake.

Records each build's ``total_cost_usd``; ``spent_today()`` sums the current UTC
day (date rollover resets to 0, mirroring ``throttle.py``). Atomic write via
``os.replace``. ``JARVIS_EVOLUTION_DAILY_USD`` (default 6.0) is the daily ceiling
the governance gate checks against — cost is the brake, not a build count.
"""
from __future__ import annotations

import json
import os
import time

from pipeline.automod._state import cost_ledger_path

DEFAULT_DAILY_USD = 6.0


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def daily_usd() -> float:
    try:
        return float(os.environ.get("JARVIS_EVOLUTION_DAILY_USD", str(DEFAULT_DAILY_USD)))
    except (TypeError, ValueError):
        return DEFAULT_DAILY_USD


def _read() -> dict:
    p = cost_ledger_path()
    if not p.exists():
        return {"date": _today(), "entries": []}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"date": _today(), "entries": []}
    if d.get("date") != _today():
        # New UTC day — yesterday's spend no longer counts.
        return {"date": _today(), "entries": []}
    return d


def spent_today() -> float:
    return round(sum(float(e.get("cost_usd", 0) or 0) for e in _read().get("entries", [])), 6)


def record(build_id: str, cost_usd: float) -> None:
    d = _read()
    d["entries"].append({"id": build_id, "cost_usd": float(cost_usd or 0), "ts": _today()})
    p = cost_ledger_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d), encoding="utf-8")
    os.replace(tmp, p)


def record_from_result(build_id: str, result_path: str) -> float:
    """Parse a ``jarvis -p --output-format json`` result file, record its
    ``total_cost_usd`` to the daily ledger, and return the cost.

    The CLI prefixes stdout with a non-JSON line (``[jarvis] proxy: …``), so we
    scan for the line that parses as a JSON object carrying ``total_cost_usd``.
    Best-effort: returns 0.0 and records nothing if missing/unparseable. Records
    for BOTH passed and failed builds — a failed build still spent tokens.
    """
    from pathlib import Path

    cost = 0.0
    try:
        for line in Path(result_path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "total_cost_usd" in obj:
                cost = float(obj.get("total_cost_usd", 0) or 0)
    except OSError:
        return 0.0
    if cost > 0:
        record(build_id, cost)
    return cost
