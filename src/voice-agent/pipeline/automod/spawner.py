"""Async subprocess spawner for auto-mod intents (Spec B, Plane 3).

drain_queue() reads ~/.jarvis/auto-mods/queue.jsonl, gates each entry
through throttle.admit_intent(), and on admit launches
`bin/jarvis-automod-impl <intent_file>` via
asyncio.create_subprocess_exec.

Lockfile (fcntl.flock exclusive) serializes spawns globally -- at most
one auto-mod subprocess runs at a time. Per-topic in-flight cap = 1
is naturally enforced by this.

Timeout: SPAWN_TIMEOUT_S (10 min). Belt + suspenders with the shell
wrapper's own `timeout 600`.

Gated by JARVIS_AUTOMOD_SPAWN_LIVE=1. When unset, drain_queue() is a
no-op (queue intact, intents accumulate for later inspection).

Spec: docs/superpowers/specs/2026-05-24-jarvis-source-code-self-mod-design.md
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from pathlib import Path

from pipeline.automod import artifact, throttle
from pipeline.automod._state import (
    intent_file_path,
    lockfile_path,
    queue_path,
)

logger = logging.getLogger("jarvis.automod.spawner")

SPAWN_TIMEOUT_S = 600

# Absolute path to the wrapper script (repo_root/bin/jarvis-automod-impl).
# This file lives at .../src/voice-agent/pipeline/automod/spawner.py, so:
#   parents[0] = automod/
#   parents[1] = pipeline/
#   parents[2] = voice-agent/
#   parents[3] = src/
#   parents[4] = repo root
WRAPPER_SCRIPT = Path(__file__).resolve().parents[4] / "bin" / "jarvis-automod-impl"


def _spawn_live() -> bool:
    return os.environ.get("JARVIS_AUTOMOD_SPAWN_LIVE", "0") == "1"


@contextlib.contextmanager
def _global_lock():
    """Exclusive lockfile via fcntl.flock -- at most one spawn at a time."""
    p = lockfile_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    import fcntl
    fd = open(p, "a+", encoding="utf-8")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except (OSError, IOError):
            pass
        fd.close()


def _read_queue() -> list[dict]:
    p = queue_path()
    if not p.exists():
        return []
    out: list[dict] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            logger.warning("[automod] dropped malformed queue entry: %r", line[:120])
    return out


def _truncate_queue() -> None:
    """Drain queue.jsonl after processing (no retries)."""
    p = queue_path()
    if p.exists():
        p.write_text("", encoding="utf-8")


async def _spawn_one(intent: dict) -> str:
    """Launch the wrapper script for a single intent. Returns
    'spawned' / 'timeout' / 'error'."""
    rec_id = intent["id"]
    intent_file = intent_file_path(rec_id)
    intent_file.parent.mkdir(parents=True, exist_ok=True)
    intent_file.write_text(
        f"INTENT: {intent['intent']}\n"
        f"RATIONALE: {intent.get('rationale', '')}\n"
        f"KIND: {intent.get('kind', 'unknown')}\n",
        encoding="utf-8",
    )

    if not WRAPPER_SCRIPT.exists():
        logger.error("[automod] wrapper missing: %s", WRAPPER_SCRIPT)
        artifact.audit("automod_spawn_error", id=rec_id,
                       error="wrapper script missing")
        return "error"

    artifact.audit("automod_spawning", id=rec_id, intent_kind=intent.get("kind"))
    logger.info("[automod] spawning: id=%s timeout=%ss",
                rec_id, SPAWN_TIMEOUT_S)

    try:
        proc = await asyncio.create_subprocess_exec(
            str(WRAPPER_SCRIPT),
            str(intent_file),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=SPAWN_TIMEOUT_S)
        artifact.audit("automod_spawn_complete", id=rec_id,
                       exit_code=proc.returncode)
        return "spawned"
    except asyncio.TimeoutError:
        logger.warning("[automod] spawn timed out: id=%s", rec_id)
        artifact.audit("automod_spawn_timeout", id=rec_id)
        return "timeout"
    except Exception as e:  # noqa: BLE001
        logger.warning("[automod] spawn error: id=%s err=%s", rec_id, e)
        artifact.audit("automod_spawn_error", id=rec_id, error=str(e))
        return "error"


async def drain_queue() -> int:
    """Drain queue.jsonl: for each intent, gate via throttle; on admit,
    spawn the wrapper. Returns count of successfully launched spawns.

    Always-safe. No-op when JARVIS_AUTOMOD_SPAWN_LIVE != '1'."""
    if not _spawn_live():
        logger.debug("[automod] spawn disabled (shadow mode)")
        return 0

    queue = _read_queue()
    if not queue:
        return 0

    spawned = 0
    with _global_lock():
        for intent in queue:
            ok, reason = throttle.admit_intent(intent)
            if not ok:
                logger.info("[automod] intent rejected by throttle: id=%s reason=%s",
                            intent.get("id"), reason)
                artifact.audit("automod_rejected", id=intent.get("id"),
                               reason=reason)
                continue
            status = await _spawn_one(intent)
            if status == "spawned":
                throttle.mark_admitted(intent["id"])
                spawned += 1
            # Timeout/error are consumed (don't retry).
        _truncate_queue()
    if spawned:
        logger.info("[automod] drain complete: spawned=%d", spawned)
    return spawned
