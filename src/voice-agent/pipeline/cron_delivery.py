"""Scheduler delivery: desktop notification + a voice queue drained on the
next session connect. No network, no LLM. SILENT jobs deliver nothing."""
from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import subprocess

try:
    import fcntl  # POSIX file locking
    msvcrt = None
except ImportError:  # Windows has no fcntl — fall back to msvcrt.locking
    fcntl = None
    import msvcrt

from pipeline import cron_jobs as cj

logger = logging.getLogger("jarvis.cron_delivery")

MAX_DIGEST_ITEMS = int(os.environ.get("JARVIS_CRON_DIGEST_MAX", "5"))
# How often the voice agent re-checks the queue DURING a live session, so a
# job firing mid-session is voiced promptly (not only on the next connect).
PENDING_POLL_S = int(os.environ.get("JARVIS_CRON_PENDING_POLL_S", "15"))


@contextlib.contextmanager
def _pending_lock():
    """Cross-process exclusive lock on pending.jsonl. The jarvis-cron.timer
    process appends (queue_pending) and the voice agent reads+clears
    (drain_pending); without this, an append landing between drain's read and
    clear would be silently lost. Uses fcntl.flock on POSIX and msvcrt.locking
    on Windows (which has no fcntl — the bare `import fcntl` previously crashed
    EVERY voice session at the entrypoint; caught on the 2026-06-18 Windows
    deploy)."""
    cj.ensure_dirs()
    # Empty lock file; encoding is harmless but quiets the cross-platform
    # checker (Windows defaults to cp1252 otherwise).
    f = open(cj.CRON_DIR / ".pending.lock", "w", encoding="utf-8")
    try:
        if fcntl is not None:
            fcntl.flock(f, fcntl.LOCK_EX)
        else:
            # Windows: exclusive-lock a 1-byte region. LK_LOCK blocks (~10 s of
            # retries) until free; queue_pending / drain_pending hold it only
            # briefly. If it can't acquire, proceed best-effort rather than
            # crash the voice session — the race it guards is rare.
            try:
                msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
            except OSError:
                pass
        yield
    finally:
        if fcntl is None and msvcrt is not None:
            try:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        f.close()  # releasing the fd releases a POSIX flock


def notify(title: str, body: str) -> None:
    """Fire a desktop notification; no-op (logged) if notify-send is absent."""
    if not shutil.which("notify-send"):
        logger.info("[cron] notify-send unavailable; notify skipped: %s", body[:80])
        return
    try:
        subprocess.run(["notify-send", title, body[:400]], timeout=5, check=False)
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("[cron] notify-send failed: %s", e)


def queue_pending(job_name: str, text: str) -> None:
    """Append a result for the voice agent to read out (on next connect, or
    mid-session via the pending watcher)."""
    with _pending_lock():
        line = json.dumps({"job": job_name, "text": text})
        with open(cj.PENDING_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def drain_pending(prefix: str = "While you were away: ") -> str:
    """Read + clear the pending queue under the cross-process lock, returning a
    voice digest ('' if empty). `prefix` lets the mid-session watcher use live
    wording instead of the connect-time 'While you were away:'."""
    with _pending_lock():
        if not cj.PENDING_FILE.exists():
            return ""
        items = []
        for ln in cj.PENDING_FILE.read_text("utf-8").splitlines():
            ln = ln.strip()
            if ln:
                try:
                    items.append(json.loads(ln))
                except json.JSONDecodeError:
                    continue
        cj.PENDING_FILE.write_text("", encoding="utf-8")
    if not items:
        return ""
    shown = items[-MAX_DIGEST_ITEMS:]
    tail = f" (and {len(items) - len(shown)} more)" if len(items) > len(shown) else ""
    parts = [f"{it['job']}: {it['text']}" for it in shown
             if isinstance(it, dict) and "job" in it and "text" in it]
    return prefix + "; ".join(parts) + tail
