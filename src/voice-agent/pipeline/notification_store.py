"""Local store for captured desktop notifications.

The D-Bus listener (pipeline.notification_listener) writes; the `notifications`
tool reads. Ring buffer of the last MAX_KEEP entries at
~/.jarvis/notifications.jsonl, mode 600 — notification bodies can be sensitive.
Honors JARVIS_HOME (tests / profile isolation).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

MAX_KEEP = 200


def _path() -> Path:
    home = os.environ.get("JARVIS_HOME") or str(Path.home() / ".jarvis")
    return Path(home) / "notifications.jsonl"


def append(app: str, summary: str, body: str) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "ts": time.time(),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "app": str(app or "")[:200],
        "summary": str(summary or "")[:500],
        "body": str(body or "")[:2000],
    }
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    _prune(p)


def _prune(p: Path) -> None:
    # Lazy prune: only rewrite when the file grows past 2x the cap, so the common
    # path is a cheap append.
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    if len(lines) <= MAX_KEEP * 2:
        return
    tmp = p.with_suffix(".jsonl.tmp")
    tmp.write_text("\n".join(lines[-MAX_KEEP:]) + "\n", encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, p)


def read(limit: int = 10, since_seconds: float | None = None) -> list[dict]:
    """Most-recent-first list of captured notifications."""
    p = _path()
    if not p.exists():
        return []
    out: list[dict] = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        return []
    if since_seconds is not None:
        cutoff = time.time() - since_seconds
        out = [r for r in out if float(r.get("ts", 0) or 0) >= cutoff]
    return out[-limit:][::-1]
