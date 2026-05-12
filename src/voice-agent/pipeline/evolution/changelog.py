"""Human-readable changelog of autonomous evolution actions.

JARVIS evolves himself without asking. Every tier transition (auto-stage,
auto-archive, promotion, quarantine, rollback) drops one Markdown entry
into ~/Documents/jarvis-evolution/YYYY-MM-DD.md so the user can review at
leisure rather than being interrupted in voice.

Multi-process safe via fcntl.flock — LiveKit's forkserver spins up
multiple workers and any of them might fire a lifecycle transition.
Best-effort: failures never raise (the rule mutation has already happened
when we get here; losing a changelog entry must not crash the agent).
"""
from __future__ import annotations

import fcntl
import logging
import time
from pathlib import Path
from typing import Optional


__all__ = ["CHANGELOG_DIR", "append"]


CHANGELOG_DIR: Path = Path.home() / "Documents" / "jarvis-evolution"

logger = logging.getLogger("jarvis.evolution.changelog")


def _today_path() -> Path:
    return CHANGELOG_DIR / f"{time.strftime('%Y-%m-%d', time.gmtime())}.md"


def _today_header() -> str:
    return (
        f"# JARVIS evolution log — {time.strftime('%Y-%m-%d', time.gmtime())}\n\n"
        "Autonomous self-evolution actions. Each entry is one tier\n"
        "transition that JARVIS made on his own. Review at leisure.\n\n"
        "---\n"
    )


def append(
    *,
    action: str,
    rule_id: str,
    rule_text: str,
    source: Optional[str] = None,
    reason: Optional[str] = None,
    evidence_turns: Optional[list[str]] = None,
    extras: Optional[dict] = None,
) -> None:
    """Append one entry. Best-effort; swallows all errors."""
    try:
        CHANGELOG_DIR.mkdir(parents=True, exist_ok=True)
        path = _today_path()
        is_new = not path.exists()
        ts = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
        lines = [
            "",
            f"## {ts} — {action.upper()} {rule_id}",
            "",
            f"**Rule:** {(rule_text or '').strip()[:300]}",
        ]
        if source:
            lines.append(f"**Source:** {source}")
        if reason:
            lines.append(f"**Reason:** {reason}")
        if evidence_turns:
            preview = ", ".join(str(t) for t in evidence_turns[:8])
            if len(evidence_turns) > 8:
                preview += f", … (+{len(evidence_turns) - 8} more)"
            lines.append(f"**Evidence turns:** {preview}")
        if extras:
            for k, v in extras.items():
                lines.append(f"**{k}:** {v}")
        lines.append("")
        entry = "\n".join(lines) + "\n"
        with path.open("a+", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                if is_new:
                    f.seek(0)
                    f.write(_today_header())
                f.write(entry)
                f.flush()
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        logger.info(f"[changelog] {action} {rule_id} → {path.name}")
    except Exception as e:
        logger.warning(f"[changelog] failed to append: {e}")
