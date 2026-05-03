"""JARVIS event hub daemon.

Reads `events:*` Redis Streams via consumer groups, applies events
idempotently to ~/.jarvis/hub/state.db, re-publishes normalized
events to `broadcasts:*` streams.

`consume_once` is the unit-testable single-batch consumer. The async
`main()` (added in Task 4) wraps it in a long-running loop with
graceful shutdown.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger("jarvis.hub")

# Resolved at import time so tests can patch.
SCHEMA_PATH = Path(__file__).parent / "schema.sql"

EVENTS_STREAM = "events:conversation"
BROADCASTS_STREAM = "broadcasts:conversation"
SETTINGS_EVENTS_STREAM = "events:settings"
SETTINGS_BROADCASTS_STREAM = "broadcasts:settings"
GROUP = "hub"
CONSUMER = "hub-1"
SETTINGS_CONSUMER = "hub-settings-1"


def bootstrap_schema(db_path: Path | str) -> None:
    """Apply schema.sql to the state DB. Idempotent."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    sql = SCHEMA_PATH.read_text()
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(sql)
        conn.commit()
    finally:
        conn.close()


async def _ensure_group(redis: Any, stream: str = EVENTS_STREAM) -> None:
    """Create the consumer group if it doesn't exist. BUSYGROUP is
    the expected error when the group already exists."""
    try:
        await redis.xgroup_create(stream, GROUP, id="0", mkstream=True)
    except Exception as e:
        if "BUSYGROUP" not in str(e):
            raise


def _apply_event(conn: sqlite3.Connection, evt: dict) -> None:
    """Apply ONE event to the state DB. Caller wraps in transaction."""
    t = evt["type"]
    src = evt["source"]
    sid = evt["session_id"]
    ts = int(evt.get("source_ts", 0))
    seid = evt["source_event_id"]
    payload = evt.get("payload", {}) or {}

    if t == "conversation.session.started":
        conn.execute(
            "INSERT OR IGNORE INTO sessions "
            "(id, source, title, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (sid, src, payload.get("title"), ts, ts),
        )
    elif t == "conversation.session.ended":
        conn.execute(
            "UPDATE sessions SET ended_at = ?, updated_at = ? WHERE id = ?",
            (ts, ts, sid),
        )
    elif t == "conversation.message.created":
        # Auto-create session if missing — handles out-of-order delivery.
        conn.execute(
            "INSERT OR IGNORE INTO sessions "
            "(id, source, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (sid, src, ts, ts),
        )
        try:
            tool_calls = payload.get("tool_calls")
            conn.execute(
                "INSERT INTO messages "
                "(session_id, source, source_event_id, role, text, "
                " tool_calls_json, ts) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    sid, src, seid,
                    payload["role"], payload["text"],
                    json.dumps(tool_calls) if tool_calls else None,
                    ts,
                ),
            )
            conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (ts, sid),
            )
        except sqlite3.IntegrityError as e:
            # UNIQUE(source, source_event_id) hit → idempotent no-op.
            if "UNIQUE constraint failed" in str(e):
                logger.debug("[hub] dedupe: %s/%s already applied", src, seid)
            else:
                raise
    elif t == "settings.value.changed":
        key = payload["key"]
        value = payload["value"]
        conn.execute(
            "INSERT INTO settings (key, value, updated_at, source) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET "
            "  value = excluded.value, "
            "  updated_at = excluded.updated_at, "
            "  source = excluded.source",
            (key, value, ts, src),
        )
    else:
        logger.warning("[hub] unknown event type: %s", t)


async def main() -> None:
    """Long-running daemon entry point. Runs three coroutines in
    parallel via asyncio.gather:
      1. events:conversation consumer
      2. events:settings consumer
      3. settings file watcher (publishes to events:settings)
    Graceful shutdown on SIGINT/SIGTERM via shared stop Event."""
    import asyncio
    import os
    import signal

    import redis.asyncio as aredis
    # Sibling module — use absolute path so this works whether the
    # daemon was launched as `python -m hub.server` or via the test
    # harness which adds src/hub to sys.path directly.
    try:
        from hub import settings_watcher
    except ImportError:
        import settings_watcher

    url = os.environ.get("JARVIS_HUB_URL", "redis://127.0.0.1:6379")
    db_path = os.environ.get(
        "JARVIS_HUB_DB",
        str(Path.home() / ".jarvis" / "hub" / "state.db"),
    )

    bootstrap_schema(db_path)
    logger.info("[hub] state.db ready at %s", db_path)

    redis = aredis.from_url(url, decode_responses=True)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    # ── Three watched files (NOT keys.env — sensitive blocklist) ──
    home = Path.home()
    WATCHED_SETTINGS = {
        "cli-model":    home / ".jarvis" / "cli-model",
        "voice-model":  home / ".jarvis" / "voice-model",
        "tts-provider": home / ".jarvis" / "tts-provider",
    }

    async def _consumer_loop(
        events_stream: str,
        broadcasts_stream: str,
        consumer: str,
        broadcasts_maxlen: int,
    ) -> None:
        logger.info(
            "[hub] consumer up — %s → %s", events_stream, broadcasts_stream,
        )
        while not stop.is_set():
            n = await consume_once(
                redis,
                db_path=db_path,
                count=100,
                events_stream=events_stream,
                broadcasts_stream=broadcasts_stream,
                consumer=consumer,
                broadcasts_maxlen=broadcasts_maxlen,
            )
            if n == 0:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=0.25)
                except asyncio.TimeoutError:
                    pass

    async def _watcher_loop() -> None:
        logger.info(
            "[hub] settings watcher up — %d files", len(WATCHED_SETTINGS),
        )
        watcher_state: dict[str, str] = {}
        while not stop.is_set():
            try:
                await settings_watcher.scan_once(
                    redis, WATCHED_SETTINGS, watcher_state,
                )
            except Exception:
                logger.exception("[hub] settings_watcher.scan_once failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass

    await asyncio.gather(
        _consumer_loop(EVENTS_STREAM, BROADCASTS_STREAM, CONSUMER, 10000),
        _consumer_loop(
            SETTINGS_EVENTS_STREAM, SETTINGS_BROADCASTS_STREAM,
            SETTINGS_CONSUMER, 1000,
        ),
        _watcher_loop(),
    )

    await redis.aclose()
    logger.info("[hub] daemon shutting down cleanly")


async def consume_once(
    redis: Any,
    db_path: str | Path | None = None,
    count: int = 100,
    block_ms: int = 0,
    *,
    events_stream: str = EVENTS_STREAM,
    broadcasts_stream: str = BROADCASTS_STREAM,
    consumer: str = CONSUMER,
    broadcasts_maxlen: int = 10000,
) -> int:
    """Consume up to `count` events from `events_stream`, apply to
    state.db, ACK, fan out to `broadcasts_stream`. Returns the
    number of events processed.

    Idempotent on duplicate `source_event_id`s via UNIQUE constraints.
    Failures ACK regardless — dead letters are out of scope.

    Multiple stream pairs (e.g. events:conversation + events:settings)
    are supported by calling this function twice in parallel with
    different `consumer` names so XREADGROUP offsets don't collide.
    """
    if db_path is None:
        db_path = Path.home() / ".jarvis" / "hub" / "state.db"

    await _ensure_group(redis, events_stream)

    resp = await redis.xreadgroup(
        GROUP, consumer,
        streams={events_stream: ">"},
        count=count,
        block=block_ms,
    )
    if not resp:
        return 0

    conn = sqlite3.connect(str(db_path))
    try:
        applied = 0
        for _stream, entries in resp:
            for entry_id, fields in entries:
                applied_ok = False
                evt = None
                try:
                    evt = json.loads(fields["data"])
                    _apply_event(conn, evt)
                    applied_ok = True
                    applied += 1
                except Exception:
                    logger.exception(
                        "[hub] failed to apply entry %s on %s; ACKing anyway",
                        entry_id, events_stream,
                    )
                await redis.xack(events_stream, GROUP, entry_id)
                if applied_ok and evt is not None:
                    try:
                        await redis.xadd(
                            broadcasts_stream,
                            {"data": json.dumps(evt)},
                            maxlen=broadcasts_maxlen,
                            approximate=True,
                        )
                    except Exception:
                        logger.exception(
                            "[hub] broadcast to %s failed for %s",
                            broadcasts_stream, entry_id,
                        )
        conn.commit()
        return applied
    finally:
        conn.close()


if __name__ == "__main__":
    import asyncio
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(main())
