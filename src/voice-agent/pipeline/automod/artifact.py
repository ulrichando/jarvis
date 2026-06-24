"""Artifact (auto-mod proposal JSON) + audit log helpers.

Atomic-write via tempfile + os.replace. Audit log filters
'anchor_path' entries starting with '/tmp/pytest-' (pre-existing
pollution from Spec A's audit at ~/.jarvis/evolution_log.jsonl).

Spec: docs/superpowers/specs/2026-05-24-jarvis-source-code-self-mod-design.md
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path

from pipeline.automod._state import (
    artifact_path,
    evolution_log_path,
)

logger = logging.getLogger("jarvis.automod.artifact")


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def write(art: dict) -> Path:
    """Atomic write of artifact JSON to ~/.jarvis/auto-mods/<id>.json.
    Required: art['id']."""
    if not art.get("id"):
        raise ValueError("artifact dict must have an 'id' field")
    p = artifact_path(art["id"])
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".art_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(art, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, str(p))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return p


def load(automod_id: str) -> dict:
    """Read and parse artifact JSON for the given automod_id."""
    p = artifact_path(automod_id)
    return json.loads(p.read_text(encoding="utf-8"))


def update_status(automod_id: str, status: str, **extra) -> dict:
    """Read artifact, set status + extra fields, write back atomically.
    Returns the updated dict."""
    art = load(automod_id)
    art["status"] = status
    for k, v in extra.items():
        art[k] = v
    write(art)
    return art


def audit(kind: str, **fields) -> None:
    """Append a record to ~/.jarvis/evolution_log.jsonl. Drops entries
    whose 'anchor_path' starts with '/tmp/pytest-' (filter the pre-
    existing pollution source from the retired rule-evolution system)."""
    anchor = fields.get("anchor_path")
    if isinstance(anchor, str) and anchor.startswith("/tmp/pytest-"):
        logger.debug("[automod] dropped pytest-tmp audit record: kind=%s", kind)
        return
    record = {"ts": _now_iso(), "kind": kind}
    record.update(fields)
    p = evolution_log_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False)
    with p.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def cleanup_artifacts(*, max_age_days: int = 30, max_log_bytes: int = 1_000_000) -> int:
    """Remove artifact JSONs older than `max_age_days` and truncate logs
    exceeding `max_log_bytes`. Called by the nightly pass so old proposals
    don't accumulate indefinitely. Returns count of files removed.

    Never raises — any failure logs and returns 0.
    """
    removed = 0
    home = Path(str(artifact_path("_")))  # dummy to get the dir
    home_dir = home.parent
    if not home_dir.exists():
        return 0
    cutoff = time.time() - max_age_days * 86400
    try:
        for p in home_dir.glob("automod-*.json"):
            try:
                if p.stat().st_mtime < cutoff:
                    # Also remove the companion log + intent files if present.
                    stem = p.stem
                    for suffix in (".log", ".intent.txt"):
                        companion = home_dir / f"{stem}{suffix}"
                        try:
                            companion.unlink()
                        except FileNotFoundError:
                            pass
                    p.unlink()
                    removed += 1
            except OSError:
                continue
        # Truncate oversized log files (keep last ~1MB).
        for log_path in home_dir.glob("automod-*.log"):
            try:
                if log_path.stat().st_size > max_log_bytes:
                    tail = log_path.read_bytes()[-max_log_bytes:]
                    log_path.write_bytes(tail)
            except OSError:
                continue
    except Exception:
        logger.debug("[automod] artifact cleanup failed", exc_info=True)
    if removed:
        logger.info("[automod] artifact cleanup: removed %d old artifact(s)", removed)
    return removed
