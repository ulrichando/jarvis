"""Logging handler that captures recurring exceptions for the auto-mod
error-driven branch (Spec 2026-05-27).

This module is import-safe — no side effects at import time. The
handler is installed at session-start via `install_error_handler()`
(added in Task 3)."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
import traceback
from pathlib import Path
from typing import Any

logger = logging.getLogger("jarvis.automod.error_logger")

# Exceptions we never propose fixes for. Lifecycle / framework / external.
# Env override: JARVIS_AUTOMOD_ERROR_IGNORE_EXC="X,Y,Z" extends (not
# replaces) this set.
_DEFAULT_IGNORE_EXC = frozenset({
    "CancelledError", "KeyboardInterrupt", "SystemExit", "GeneratorExit",
    "BrokenPipeError", "ConnectionResetError",
    "asyncio.CancelledError",
})

# Loggers we attach to. The "must have jarvis-owned frame" filter
# ensures we only signature our own bugs even when captured from
# upstream loggers.
_ATTACH_LOGGERS = ("jarvis", "livekit.agents")

_PROJECT_PREFIX = "src/voice-agent/"
_VENDOR_HINTS = (".venv/", "/site-packages/", "tests/")

# Thread-local reentrance guard for the handler emit() path.
_in_emit = threading.local()


def _ignore_set() -> frozenset[str]:
    """Return the active ignore-set: default plus env-var additions."""
    extra = os.environ.get("JARVIS_AUTOMOD_ERROR_IGNORE_EXC", "")
    if not extra:
        return _DEFAULT_IGNORE_EXC
    additions = frozenset(s.strip() for s in extra.split(",") if s.strip())
    return _DEFAULT_IGNORE_EXC | additions


def _telemetry_db_path() -> Path:
    p = os.environ.get("JARVIS_TURN_TELEMETRY_DB")
    if p:
        return Path(p)
    home = os.environ.get("JARVIS_HOME") or str(Path.home() / ".local/share/jarvis")
    return Path(home) / "turn_telemetry.db"


def _is_jarvis_owned(filename: str) -> bool:
    """True iff the frame file belongs to JARVIS source (not venv/vendor)."""
    if any(hint in filename for hint in _VENDOR_HINTS):
        return False
    return _PROJECT_PREFIX in filename or "/voice-agent/" in filename


def _jarvis_frames(tb) -> list[tuple[str, str]]:
    """Walk traceback, return [(rel_path, method_name)] for jarvis-owned
    frames only. Excludes venv + tests + vendor dirs."""
    out = []
    for frame in traceback.extract_tb(tb):
        if not _is_jarvis_owned(frame.filename):
            continue
        idx = frame.filename.find(_PROJECT_PREFIX)
        rel = frame.filename[idx:] if idx >= 0 else frame.filename
        out.append((rel, frame.name))
    return out


def _signature(exc_class: str, frames: list[tuple[str, str]]) -> str:
    """Stable signature: SHA1 of exc_class + sorted(set(file:method)).

    NO line numbers — they shift under unrelated edits.
    NO single-frame-only — a centralized handler would collapse every
    distinct bug into one signature. All jarvis-owned frames participate.
    """
    parts = sorted({f"{f}:{m}" for f, m in frames})
    payload = f"{exc_class}|" + "|".join(parts)
    return hashlib.sha1(payload.encode()).hexdigest()[:12]


# Fixability heuristics — cheap pre-filter so we don't burn LLM tokens
# on exceptions that no code change can fix.
_HIGH_FIXABILITY = frozenset({
    "ValueError", "TypeError", "KeyError", "AttributeError", "IndexError",
    "JSONDecodeError",
    "ValidationError",   # pydantic AND jsonschema both use this name
    "ImportError", "ModuleNotFoundError",
    "AssertionError",
})
_LOW_FIXABILITY_KEYWORDS = (
    "api_key", "401", "403", "rate limit", "quota",
    "unauthor", "forbidden",
)


def _fixability_score(exc_class: str, exc_message: str,
                      frames: list[tuple[str, str]]) -> float:
    """Return score in [0, 1]. Caller emits intent only if >= 0.5.

    Heuristics:
      +0.3 if exc_class is in _HIGH_FIXABILITY (programming bugs we caused)
      -0.4 if message contains api_key/auth/rate-limit hints
      -0.2 if the LAST jarvis frame is in providers/ or resilience/
            (typically transient external issues)
    """
    score = 0.5
    if exc_class in _HIGH_FIXABILITY:
        score += 0.3
    msg_lc = exc_message.lower()
    if any(k in msg_lc for k in _LOW_FIXABILITY_KEYWORDS):
        score -= 0.4
    if frames and ("providers/" in frames[-1][0] or "resilience/" in frames[-1][0]):
        score -= 0.2
    return max(0.0, min(1.0, score))


def _truncate_tb(tb_text: str) -> str:
    """Cap traceback to 4KB so a deeply nested exception can't blow up
    the DB row. Char-based cap, not byte-based — UTF-8 safe per row."""
    if len(tb_text) <= 4096:
        return tb_text
    return tb_text[:4093] + "..."


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _upsert(conn: sqlite3.Connection, sig: str, exc_class: str,
            exc_message: str, frames: list[tuple[str, str]],
            sample_tb: str, fixability: float) -> None:
    """Atomic upsert via SQLite ON CONFLICT — safe under forkserver
    concurrent writes. Increments count + updates last_seen on repeat."""
    frames_json = json.dumps([{"file": f, "method": m} for f, m in frames])
    now = _now_iso()
    conn.execute("""
        INSERT INTO recurring_errors
            (signature, exc_class, exc_message, first_seen, last_seen,
             count, frames_json, sample_traceback, fixability_score)
        VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
        ON CONFLICT(signature) DO UPDATE SET
            count = count + 1,
            last_seen = excluded.last_seen,
            exc_message = excluded.exc_message,
            sample_traceback = excluded.sample_traceback
    """, (sig, exc_class, exc_message[:500], now, now,
          frames_json, sample_tb, fixability))
    conn.commit()


class ErrorTelemetryHandler(logging.Handler):
    """Captures ERROR-level log records that carry an exc_info into the
    recurring_errors table. Silent on all internal failures (drops record).

    Filters:
      - record.levelno >= ERROR
      - record.exc_info is set (diagnostic logs without exception → skip)
      - exception class not in ignore set
      - at least one jarvis-owned frame present in the traceback
    """

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(_in_emit, "active", False):
            return  # reentrance guard
        _in_emit.active = True
        try:
            self._emit_impl(record)
        except Exception:
            # NEVER raise from a logging handler. Swallow.
            pass
        finally:
            _in_emit.active = False

    def _emit_impl(self, record: logging.LogRecord) -> None:
        if record.levelno < logging.ERROR:
            return
        if record.exc_info is None or record.exc_info[0] is None:
            return
        exc_class = record.exc_info[0].__name__
        if exc_class in _ignore_set():
            return
        frames = _jarvis_frames(record.exc_info[2])
        if not frames:
            return  # no jarvis-owned frame — not our bug
        exc_message = str(record.exc_info[1])
        sig = _signature(exc_class, frames)
        fixability = _fixability_score(exc_class, exc_message, frames)
        tb_text = "".join(traceback.format_exception(*record.exc_info, limit=8))
        sample_tb = _truncate_tb(tb_text)
        db = _telemetry_db_path()
        if not db.exists():
            return  # telemetry not initialized — drop
        with sqlite3.connect(str(db), timeout=2.0) as conn:
            _upsert(conn, sig, exc_class, exc_message, frames,
                    sample_tb, fixability)


# Idempotent install state.
_INSTALLED_HANDLER: ErrorTelemetryHandler | None = None


def install_error_handler() -> None:
    """Attach a single ErrorTelemetryHandler to the JARVIS + livekit
    root loggers. Idempotent: subsequent calls are no-ops."""
    global _INSTALLED_HANDLER
    if _INSTALLED_HANDLER is not None:
        return
    h = ErrorTelemetryHandler(level=logging.ERROR)
    for name in _ATTACH_LOGGERS:
        target = logging.getLogger(name)
        if not any(isinstance(existing, ErrorTelemetryHandler)
                   for existing in target.handlers):
            target.addHandler(h)
    _INSTALLED_HANDLER = h
    logger.info("[automod] error telemetry handler installed on %s",
                ", ".join(_ATTACH_LOGGERS))
