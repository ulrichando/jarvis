"""
One-shot backfill of every turn in ~/.jarvis/conversations.db into the
local Convex backend.

Safe to re-run: turns:append is idempotent on (sessionId, ts, role)
so duplicates are no-ops.

Usage:
    .venv/bin/python backfill_convex.py
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time
from pathlib import Path

from convex import ConvexClient

DB_PATH    = Path.home() / ".jarvis" / "conversations.db"
CONVEX_URL = os.environ.get("JARVIS_CONVEX_URL", "http://127.0.0.1:3210")

# Print progress every N rows so a stalled run is obvious.
PROGRESS_EVERY = 100


def main() -> int:
    if not DB_PATH.exists():
        print(f"no SQLite db at {DB_PATH}", file=sys.stderr)
        return 1

    client = ConvexClient(CONVEX_URL)
    conn = sqlite3.connect(str(DB_PATH))
    rows = conn.execute(
        "SELECT session_id, ts, role, text FROM turns ORDER BY ts ASC, id ASC",
    ).fetchall()
    conn.close()

    total = len(rows)
    print(f"backfilling {total} turns from {DB_PATH} → {CONVEX_URL}")
    started = time.time()
    written = 0
    skipped_role = 0
    skipped_empty = 0
    failed = 0

    for i, (session_id, ts_sec, role, text) in enumerate(rows, 1):
        if role not in ("user", "assistant"):
            skipped_role += 1
            continue
        text = (text or "").strip()
        if not text:
            skipped_empty += 1
            continue
        try:
            client.mutation("turns:append", {
                "sessionId": session_id,
                # SQLite stored seconds; Convex schema is ms. Stretch to
                # ms by multiplying — preserves ordering, loses sub-second
                # detail (we never had it anyway in the SQLite store).
                "ts":     int(ts_sec) * 1000,
                "role":   role,
                "text":   text,
                "source": "voice-agent-backfill",
            })
            written += 1
        except Exception as e:
            failed += 1
            if failed <= 5:
                print(f"  row {i} failed: {e}", file=sys.stderr)

        if i % PROGRESS_EVERY == 0:
            elapsed = time.time() - started
            rate = i / elapsed if elapsed > 0 else 0
            print(f"  {i}/{total} ({rate:.1f}/s)")

    elapsed = time.time() - started
    print(
        f"done in {elapsed:.1f}s — wrote={written} "
        f"skipped_role={skipped_role} skipped_empty={skipped_empty} "
        f"failed={failed}",
    )
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
