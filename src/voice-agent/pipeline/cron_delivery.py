"""Scheduler delivery: desktop notification + a voice queue drained on the
next session connect. No network, no LLM. SILENT jobs deliver nothing."""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess

from pipeline import cron_jobs as cj

logger = logging.getLogger("jarvis.cron_delivery")

MAX_DIGEST_ITEMS = int(os.environ.get("JARVIS_CRON_DIGEST_MAX", "5"))


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
    """Append a result for the next voice session to read out."""
    cj.ensure_dirs()
    line = json.dumps({"job": job_name, "text": text})
    with open(cj.PENDING_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def drain_pending() -> str:
    """Read + clear the pending queue, returning a voice digest ('' if empty)."""
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
    return "While you were away: " + "; ".join(parts) + tail
