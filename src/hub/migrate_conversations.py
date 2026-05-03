"""One-shot: port ~/.jarvis/conversations.db turns into the event hub.

Usage:
    PYTHONPATH=src python -m hub.migrate_conversations [--dry-run]

Idempotency: each turn gets a deterministic source_event_id derived
from sha256(session_id|ts|role|text). Re-runs collide on
UNIQUE(source, source_event_id) and become DB-level no-ops.

Old `turns.ts` was stored in seconds; we multiply by 1000 to match
the new envelope's ms timestamps.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

EVENTS_STREAM = "events:conversation"


def _stable_event_id(session_id: str, ts: int, role: str, text: str) -> str:
    """Deterministic id so re-runs are idempotent. SHA-256 truncated
    to 32 hex chars — collision risk negligible at our scale."""
    h = hashlib.sha256(
        f"{session_id}|{ts}|{role}|{text}".encode()
    ).hexdigest()
    return h[:32]


async def run(
    old_db_path: Path | str,
    redis: Any | None = None,
    state_db: Path | str | None = None,
    dry_run: bool = False,
) -> int:
    """Read all turns from old_db_path, publish them as conversation
    events. Returns count of `conversation.message.created` events
    that would be published. `state_db` is unused here (kept for test
    parity); the hub daemon writes state.db when it consumes.
    Sessions are derived from distinct session_id values in source rows.
    """
    old = sqlite3.connect(str(old_db_path))
    try:
        rows = old.execute(
            "SELECT session_id, ts, role, text FROM turns "
            "ORDER BY session_id, ts, id"
        ).fetchall()
    finally:
        old.close()

    if redis is None:
        import redis.asyncio as aredis
        redis = aredis.from_url(
            os.environ.get("JARVIS_HUB_URL", "redis://127.0.0.1:6379"),
            decode_responses=True,
        )

    seen_sessions: set[str] = set()
    published = 0
    for sid, ts, role, text in rows:
        ts_ms = int(ts) * 1000  # old DB stored seconds
        if sid not in seen_sessions:
            seen_sessions.add(sid)
            sess_evt = {
                "source": "voice",
                "source_event_id": _stable_event_id(sid, ts, "_session", ""),
                "type": "conversation.session.started",
                "session_id": sid,
                "source_ts": ts_ms,
                "payload": {"title": None},
            }
            if not dry_run:
                await redis.xadd(EVENTS_STREAM, {"data": json.dumps(sess_evt)})

        msg_evt = {
            "source": "voice",
            "source_event_id": _stable_event_id(sid, ts, role, text),
            "type": "conversation.message.created",
            "session_id": sid,
            "source_ts": ts_ms,
            "payload": {"role": role, "text": text},
        }
        if not dry_run:
            await redis.xadd(EVENTS_STREAM, {"data": json.dumps(msg_evt)})
        published += 1
    return published


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="report counts without publishing")
    ap.add_argument("--source-db",
                    default=str(Path.home() / ".jarvis" / "conversations.db"))
    args = ap.parse_args()
    n = asyncio.run(run(args.source_db, dry_run=args.dry_run))
    print(f"published {n} message events (dry_run={args.dry_run})")


if __name__ == "__main__":
    main()
