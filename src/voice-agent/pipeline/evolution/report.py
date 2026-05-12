"""Daily evolution report — read audit log, summarize 24h, write markdown.

Run from a 06:00 daily timer (systemd or asyncio). Reads
~/.jarvis/evolution_log.jsonl, filters by window_start, groups by
transition kind, writes ~/.jarvis/evolution_report.md.

Voice tool `evolution_report(when='today'|'week')` reads this file.
"""
from __future__ import annotations

import json
import logging
import time
from collections import Counter
from pathlib import Path
from typing import Optional

from . import audit_log


__all__ = ["REPORT_PATH", "write_daily"]


logger = logging.getLogger("jarvis.evolution.report")


REPORT_PATH: Path = Path.home() / ".jarvis" / "evolution_report.md"


def _read_events() -> list[dict]:
    if not audit_log.LOG_PATH.exists():
        return []
    out: list[dict] = []
    for line in audit_log.LOG_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _within(event: dict, window_start: str) -> bool:
    return str(event.get("ts", "")) >= window_start


def write_daily(*, window_start: Optional[str] = None) -> None:
    if window_start is None:
        window_start = time.strftime(
            "%Y-%m-%dT00:00:00Z", time.gmtime()
        )
    events = [e for e in _read_events() if _within(e, window_start)]

    transitions = [e for e in events if e.get("kind") == "tier_transition"]
    by_to: Counter[str] = Counter(e.get("to_tier", "?") for e in transitions)
    proposals_logged = sum(
        1 for e in events
        if e.get("kind") in ("live_capture_proposal", "would_stage")
    )
    promoted = by_to.get("accepted", 0)
    staged_today = sum(
        1 for e in transitions
        if e.get("from_tier") == "proposed" and e.get("to_tier") == "staged"
    )
    archived_today = by_to.get("archived", 0)
    hitl_queued = sum(
        1 for e in events
        if e.get("kind") in ("archival_routed_to_hitl", "core_promotion_proposed")
    )

    lines: list[str] = []
    lines.append(f"# JARVIS Evolution Report — {window_start[:10]}")
    lines.append("")
    if not events:
        lines.append("_No evolution activity in this window._")
    else:
        lines.append("## Summary")
        lines.append("")
        lines.append(f"- {staged_today} staged")
        lines.append(f"- {promoted} promoted to accepted")
        lines.append(f"- {archived_today} archived")
        lines.append(f"- {hitl_queued} HITL items pending")
        lines.append(f"- {proposals_logged} live-capture / would-stage proposals logged")
        lines.append("")
        lines.append("## Transitions")
        lines.append("")
        for e in transitions:
            lines.append(
                f"- `{e['ts'][:19]}` `{e['rule_id']}` "
                f"**{e.get('from_tier', '?')}** → **{e.get('to_tier', '?')}** "
                f"— {e.get('reason', '')}"
            )
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info(f"[report] wrote {REPORT_PATH}")
