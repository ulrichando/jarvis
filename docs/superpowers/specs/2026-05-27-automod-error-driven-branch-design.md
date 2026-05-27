# Auto-mod error-driven branch — design

**Date:** 2026-05-27
**Status:** spec, pre-implementation
**Author:** Ulrich + Claude
**Scope:** `src/voice-agent/pipeline/automod/error_logger.py` (new), `src/voice-agent/pipeline/automod/error_log_fallback.py` (new), `src/voice-agent/pipeline/automod/patterns.py` (extend), `src/voice-agent/pipeline/turn_telemetry.py` (new table), `src/voice-agent/jarvis_agent.py` (handler install), 6 unit-test files (new).

**Out of scope:** No changes to spawner / finalize / merge CLI / blocklist / wrapper script. No real-time notifications. No telemetry pruning policy (note as v2 follow-up). No iterative subagent refinement (one-shot via existing wrapper is fine for v1).

## TL;DR

Extend the existing auto-mod loop with a third detection scanner that catches RECURRING EXCEPTIONS from JARVIS's own code and emits proposal intents the same way the existing correction + confab scanners do. Source-of-truth is a new SQLite table populated by a `logging.Handler` attached to both the `jarvis` and `livekit.agents` root loggers; a direct-tail of `voice-agent.log` exists as a cold-start fallback. Signature scheme is line-number-independent and includes all jarvis-owned frames (not just the top). Dual threshold gate fires on either bursts (3+ in 2h) or chronic drips (10+ in 7d). A cheap fixability score filters out exceptions LLM fixes can't help with (transient connection errors, provider auth failures, asyncio lifecycle).

## Why now

1. **JARVIS produces real exceptions today that no system surfaces.** The 22:01:23 turn earlier today showed `unchecked` confab state on a story-completion turn that should not have been route=EMOTIONAL — likely an underlying classifier exception that swallowed silently. Every `try/except Exception: logger.debug(...)` site in `jarvis_agent.py` (there are 50+) is a place a real bug can hide under debug-level visibility forever.
2. **The auto-mod loop already exists for corrections + confabs.** Errors are the obvious third detection class. The scanner pattern (`_scan_*(conn) -> int`) and queue contract are stable. Net new code is one scanner + one capture path + one fallback.
3. **Research validates the architecture.** Sentry/Rollbar/Datadog converge on hash-based signatures excluding line numbers + dual-threshold (rate + count) gates + cheap fixability pre-filter. Industry consensus aligns with what we'd build.
4. **High-fixability target class is real.** SelfHeal (2025) and AgentFixer (2025) both find that ~38% of LLM-agent runtime failures are parsing/schema bugs — exactly the class of exception JARVIS's `anthropic_strict_schema` sanitizer already addresses. Auto-mod can close the loop on the rest.

## Background — what's there today

- **Existing automod** (`src/voice-agent/pipeline/automod/`, 8 modules, 1142 lines):
  - `_state.py` — paths, HARD_BLOCKLIST, `is_blocked_path()` helper
  - `patterns.py` — `_scan_corrections`, `_scan_confabs`, `scan_and_emit()`; THRESHOLD=3, CONFAB_WINDOW_DAYS=7
  - `spawner.py` — reads `queue.jsonl`, gates via throttle, forks `bin/jarvis-automod-impl` wrapper
  - `test_gate.py` — diff-scope assertion at finalize time (blocklist + max-files + max-lines + no-test-deletion)
  - `throttle.py` — daily cap (`JARVIS_AUTOMOD_DAILY_CAP`, default 3 per kind per day)
  - `finalize.py` — collects subagent output into `<id>.json` artifact
  - `cli.py` — `bin/jarvis-automod list|show|merge|reject|revert`
- **Queue contract** (`~/.jarvis/auto-mods/queue.jsonl`): one record per line, fields: `id`, `kind`, `intent`, `rationale`, `evidence`, `created_at`. Spawner only propagates `id / intent / rationale / kind` to the wrapper.
- **Wrapper subagent** (`bin/jarvis-automod-impl`): receives `<id>.intent.txt`, branches from master, runs `bin/jarvis -p` with constraints (HARD RULES include the HARD_BLOCKLIST verbatim), commits if pytest stays green, calls `finalize.py`.
- **HARD_BLOCKLIST** (`_state.py:57`): `src/voice-agent/sanitizers/`, `confab_detector.py`, `pipeline/automod/`, `pipeline/skill_review.py`, `prompts/soul.md`, `CLAUDE.md`, `regression-prevention.md`, `MEMORY.md`, `USER.md`. Includes `pipeline/automod/` — so this PR's own files are protected from being touched by future auto-mod proposals.

## Part 1 — Storage (new SQLite table)

Add to `src/voice-agent/pipeline/turn_telemetry.py::init_db`, idempotent CREATE TABLE:

```sql
CREATE TABLE IF NOT EXISTS recurring_errors (
    signature TEXT PRIMARY KEY,           -- SHA1 hex, 12 chars
    exc_class TEXT NOT NULL,              -- e.g. "ValueError"
    exc_message TEXT,                     -- last seen, truncated 500 chars
    first_seen TEXT NOT NULL,             -- ISO8601 Z
    last_seen TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 1,
    frames_json TEXT NOT NULL,            -- list[{file, method}] (jarvis-owned only)
    sample_traceback TEXT,                -- last seen, 8 frames, 4KB cap
    fixability_score REAL DEFAULT 0.5,
    proposed_at TEXT                      -- NULL until intent emitted
);
CREATE INDEX IF NOT EXISTS idx_recurring_errors_last_seen
    ON recurring_errors(last_seen);
```

Rationale:
- `signature` PRIMARY KEY lets `INSERT ... ON CONFLICT(signature) DO UPDATE` work atomically without app-level locking.
- `frames_json` stores the full jarvis-owned frame stack as JSON for forensic inspection in `bin/jarvis-automod show`. The signature itself is hashed from the same data.
- `proposed_at NULL` is the "not yet proposed" sentinel; set when intent emitted.
- 4KB cap on `sample_traceback` enforced at write time (Python-side truncation).

## Part 2 — Capture path (`pipeline/automod/error_logger.py`, new)

```python
"""Logging handler that captures recurring exceptions for the auto-mod
error-driven branch (Spec 2026-05-27)."""
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

logger = logging.getLogger("jarvis.automod.error_logger")

# Exceptions we never propose fixes for. Lifecycle / framework / external.
# Env override: JARVIS_AUTOMOD_ERROR_IGNORE_EXC="CancelledError,X,Y"
_DEFAULT_IGNORE_EXC = frozenset({
    "CancelledError", "KeyboardInterrupt", "SystemExit", "GeneratorExit",
    "BrokenPipeError", "ConnectionResetError",
    "asyncio.CancelledError",   # qualname variant
})

# Loggers we attach to. The "jarvis-owned frame required" filter
# ensures we only signature our own bugs even when captured from
# upstream loggers (livekit framework code that exception-bubbles
# through our handlers).
_ATTACH_LOGGERS = ("jarvis", "livekit.agents")

_PROJECT_PREFIX = "src/voice-agent/"
_VENDOR_HINTS   = (".venv/", "/site-packages/", "tests/")

# Thread-local reentrance guard. If emit() itself raises and that
# error gets logged, we'd recurse forever. The guard drops re-entry.
_in_emit = threading.local()


def _ignore_set() -> frozenset[str]:
    extra = os.environ.get("JARVIS_AUTOMOD_ERROR_IGNORE_EXC", "")
    if not extra:
        return _DEFAULT_IGNORE_EXC
    return _DEFAULT_IGNORE_EXC | frozenset(s.strip() for s in extra.split(",") if s.strip())


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
    """Walk the traceback, return [(rel_path, method_name)] for
    jarvis-owned frames only. Excludes venv + tests + vendor dirs."""
    out = []
    for frame in traceback.extract_tb(tb):
        if not _is_jarvis_owned(frame.filename):
            continue
        # rel_path: keep only the src/voice-agent/... portion
        idx = frame.filename.find(_PROJECT_PREFIX)
        rel = frame.filename[idx:] if idx >= 0 else frame.filename
        out.append((rel, frame.name))
    return out


def _signature(exc_class: str, frames: list[tuple[str, str]]) -> str:
    """Stable signature: SHA1 of exc_class + sorted(set(file:method)).

    NO line numbers — they shift under unrelated edits and produce
    duplicate proposals for the same bug.
    NO single-frame-only — a centralized handler in jarvis_agent.py
    would collapse every distinct bug into one signature. All
    jarvis-owned frames participate."""
    parts = sorted({f"{f}:{m}" for f, m in frames})
    payload = f"{exc_class}|" + "|".join(parts)
    return hashlib.sha1(payload.encode()).hexdigest()[:12]


# Fixability heuristics — cheap pre-filter so we don't burn LLM tokens
# on exceptions that no code change can fix.
_HIGH_FIXABILITY = frozenset({
    "ValueError", "TypeError", "KeyError", "AttributeError", "IndexError",
    "JSONDecodeError",                       # parsing
    "ValidationError",                       # pydantic + jsonschema both use this name
    "ImportError", "ModuleNotFoundError",
    "AssertionError",
})
_LOW_FIXABILITY_KEYWORDS = (
    "api_key", "401", "403", "rate limit", "quota",
    "unauthor", "forbidden",
)


def _fixability_score(exc_class: str, exc_message: str,
                      frames: list[tuple[str, str]]) -> float:
    """Return a score in [0, 1]. Caller emits intent only if >= 0.5."""
    score = 0.5
    if exc_class in _HIGH_FIXABILITY:
        score += 0.3
    msg_lc = exc_message.lower()
    if any(k in msg_lc for k in _LOW_FIXABILITY_KEYWORDS):
        score -= 0.4
    # If the deepest jarvis frame is in providers/ or resilience/,
    # it's typically a transient external failure rather than a logic bug.
    if frames and ("providers/" in frames[-1][0] or "resilience/" in frames[-1][0]):
        score -= 0.2
    return max(0.0, min(1.0, score))


def _truncate_tb(tb_text: str) -> str:
    """Cap to 4 KB so a deeply nested exception doesn't blow up the DB row."""
    if len(tb_text) <= 4096:
        return tb_text
    return tb_text[:4093] + "..."


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _upsert(conn: sqlite3.Connection, sig: str, exc_class: str,
            exc_message: str, frames: list[tuple[str, str]],
            sample_tb: str, fixability: float) -> None:
    """Atomic upsert via SQLite ON CONFLICT — safe under forkserver
    concurrent writes. Increments count and updates last_seen on repeat."""
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
    recurring_errors table. Filters: must have exc_info, exc class not
    in ignore set, must have at least one jarvis-owned frame in the
    traceback. Silent on all internal failures (drops record)."""

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
            return  # diagnostic error log without exception — skip
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
            return  # telemetry not initialized yet — drop
        with sqlite3.connect(str(db), timeout=2.0) as conn:
            _upsert(conn, sig, exc_class, exc_message, frames,
                    sample_tb, fixability)


# Idempotent install. Re-attaching the same handler instance to the
# same logger would create double-counting; re-install is a no-op.
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
```

## Part 3 — Scanner (`patterns.py` extension)

Add to `pipeline/automod/patterns.py`:

```python
ERROR_BURST_WINDOW_HOURS = 2
ERROR_BURST_COUNT = 3
ERROR_DRIP_WINDOW_DAYS = 7
ERROR_DRIP_COUNT = 10
ERROR_FIXABILITY_FLOOR = 0.5


def _scan_errors(conn: sqlite3.Connection) -> int:
    """Emit intents for recurring errors that crossed either threshold.

    Burst path: count >= 3 AND occurrences in last 2h >= 3
    Drip  path: count >= 10 AND last_seen within 7 days

    Both gated on fixability_score >= 0.5 and proposed_at IS NULL.
    """
    # Fallback population if the table is empty (handler not wired yet).
    from pipeline.automod.error_log_fallback import populate_from_log_if_empty
    populate_from_log_if_empty(conn)

    burst_cutoff = _iso_offset(-ERROR_BURST_WINDOW_HOURS * 3600)
    drip_cutoff  = _iso_offset(-ERROR_DRIP_WINDOW_DAYS * 86400)

    rows = conn.execute("""
        SELECT signature, exc_class, exc_message, count,
               first_seen, last_seen, frames_json, sample_traceback,
               fixability_score
          FROM recurring_errors
         WHERE proposed_at IS NULL
           AND fixability_score >= ?
           AND (
                (count >= ? AND last_seen >= ?)            -- burst
             OR (count >= ? AND last_seen >= ?)            -- drip
           )
         ORDER BY count DESC, last_seen DESC
    """, (ERROR_FIXABILITY_FLOOR,
          ERROR_BURST_COUNT, burst_cutoff,
          ERROR_DRIP_COUNT, drip_cutoff)).fetchall()

    emitted = 0
    for (sig, exc_class, exc_msg, count, first, last,
         frames_json, sample_tb, fixability) in rows:
        rec_id = _next_id("error")
        frames = json.loads(frames_json or "[]")
        frames_text = "\n".join(f"  - {f['file']}:{f['method']}" for f in frames)
        intent_body = (
            f"Investigate a recurring exception in JARVIS's own code.\n\n"
            f"EXCEPTION: {exc_class}\n"
            f"MESSAGE:   {exc_msg!r}\n"
            f"OCCURRENCES: {count} (first seen {first}, last seen {last})\n"
            f"FIXABILITY: {fixability:.2f}\n\n"
            f"AFFECTED FILES (jarvis-owned frames in the traceback):\n"
            f"{frames_text}\n\n"
            f"SAMPLE TRACEBACK:\n"
            f"{sample_tb}\n\n"
            f"INVESTIGATE: read each affected file, identify the root cause "
            f"(may be at any frame in the stack, not just the top), and "
            f"propose a targeted fix. The fix should either prevent the "
            f"exception from being raised OR handle it cleanly when it "
            f"cannot be prevented. Do NOT add a broad except: that hides "
            f"the underlying bug."
        )
        _emit({
            "id": rec_id,
            "kind": "error",
            "intent": intent_body,
            "rationale": f"raised {count} times "
                         f"({first} → {last}); fixability={fixability:.2f}",
            "evidence": {
                "signature": sig, "exc_class": exc_class,
                "exc_message": exc_msg, "count": count,
                "first_seen": first, "last_seen": last,
                "frames": frames, "fixability_score": fixability,
            },
            "created_at": _now_iso(),
        })
        conn.execute(
            "UPDATE recurring_errors SET proposed_at=? WHERE signature=?",
            (_now_iso(), sig),
        )
        emitted += 1
    conn.commit()
    return emitted


def _iso_offset(seconds_delta: int) -> str:
    return time.strftime(
        "%Y-%m-%dT%H:%M:%SZ",
        time.gmtime(time.time() + seconds_delta),
    )
```

`scan_and_emit()` gets one extra line:

```python
emitted += _scan_corrections(conn)
emitted += _scan_confabs(conn)
emitted += _scan_errors(conn)   # NEW
```

## Part 4 — Direct-tail fallback (`pipeline/automod/error_log_fallback.py`, new)

```python
"""Fallback population of recurring_errors from voice-agent.log.

Used when the ErrorTelemetryHandler hasn't been wired yet (fresh
session, table is empty) but the log file already has 24h of
exception records we shouldn't ignore. Best-effort; failures
return 0 silently."""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
from pathlib import Path

from pipeline.automod.error_logger import (
    _is_jarvis_owned, _signature, _fixability_score, _upsert, _ignore_set,
    _truncate_tb,
)

logger = logging.getLogger("jarvis.automod.error_log_fallback")

LOG_PATH = Path.home() / ".local/share/jarvis/logs/voice-agent.log"
LOOKBACK_SECONDS = 24 * 3600

_FRAME_RE = re.compile(
    r'File "([^"]+)", line (\d+), in (\w+)',
)


def populate_from_log_if_empty(conn: sqlite3.Connection) -> int:
    """If recurring_errors is empty, seed it from the last 24h of
    voice-agent.log. Returns count of new signatures inserted."""
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM recurring_errors"
        ).fetchone()
        if row[0] > 0:
            return 0
    except sqlite3.Error:
        return 0  # table missing or other — abort cleanly

    if not LOG_PATH.exists():
        return 0

    cutoff_ts = time.time() - LOOKBACK_SECONDS
    inserted = 0
    try:
        with LOG_PATH.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                rec = _parse_log_line(line)
                if rec is None:
                    continue
                if rec["ts"] < cutoff_ts:
                    continue
                if rec["level"] != "ERROR":
                    continue
                exc_class = rec.get("exc_class")
                if not exc_class or exc_class in _ignore_set():
                    continue
                frames = _frames_from_tb(rec.get("traceback", ""))
                if not frames:
                    continue
                sig = _signature(exc_class, frames)
                sample_tb = _truncate_tb(rec.get("traceback", ""))
                exc_message = rec.get("exc_message", "")
                fixability = _fixability_score(exc_class, exc_message, frames)
                _upsert(conn, sig, exc_class, exc_message, frames,
                        sample_tb, fixability)
                inserted += 1
    except OSError:
        return 0

    if inserted:
        logger.info("[automod] fallback seeded %d records from log", inserted)
    return inserted


def _parse_log_line(line: str) -> dict | None:
    """Parse one JSON-line log record. Tolerates non-JSON lines."""
    line = line.strip()
    if not line or not line.startswith("{"):
        return None
    try:
        rec = json.loads(line)
    except json.JSONDecodeError:
        return None
    out = {
        "level": rec.get("level", ""),
        "message": rec.get("message", ""),
        "ts": _parse_ts(rec.get("timestamp")),
        "traceback": rec.get("exception", "") or rec.get("traceback", ""),
        "exc_class": None,
        "exc_message": "",
    }
    tb_text = out["traceback"]
    if tb_text:
        # The exception class is the last "ExcClass: message" line
        m = re.search(r"^([A-Za-z_][A-Za-z0-9_.]*): (.*?)$",
                      tb_text, re.MULTILINE)
        if m:
            out["exc_class"] = m.group(1).split(".")[-1]
            out["exc_message"] = m.group(2)
    return out


def _parse_ts(ts: str | None) -> float:
    if not ts:
        return 0.0
    try:
        # 2026-05-27T22:01:23.456+00:00
        from datetime import datetime
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts).timestamp()
    except (ValueError, TypeError):
        return 0.0


def _frames_from_tb(tb_text: str) -> list[tuple[str, str]]:
    """Extract [(rel_path, method)] from a traceback string. Only
    jarvis-owned frames are returned."""
    out = []
    for m in _FRAME_RE.finditer(tb_text):
        filename, _line, method = m.group(1), m.group(2), m.group(3)
        if not _is_jarvis_owned(filename):
            continue
        idx = filename.find("src/voice-agent/")
        rel = filename[idx:] if idx >= 0 else filename
        out.append((rel, method))
    return out
```

## Part 5 — Wiring (`jarvis_agent.py`)

Add ONE block at session-start, near the existing `init_db` call (around line 5327):

```python
# Install the auto-mod error telemetry handler. Captures recurring
# exceptions from this process for the auto-mod error-driven scanner.
# Idempotent — re-install is a no-op. Spec: docs/superpowers/specs/
# 2026-05-27-automod-error-driven-branch-design.md
try:
    from pipeline.automod.error_logger import install_error_handler
    install_error_handler()
except Exception as _e:
    logger.warning(f"[automod] error handler install failed: {_e}")
```

`init_db()` in `pipeline/turn_telemetry.py` gets the `CREATE TABLE IF NOT EXISTS recurring_errors` from Part 1 + an idempotent `CREATE INDEX`.

## Part 6 — Forkserver semantics

JARVIS runs as a livekit-agents worker that spawns forkserver children per job. Implications:

- `install_error_handler()` runs in each child at session-start. Idempotent (checks for an existing instance attached to each target logger). No-op on re-attach.
- The handler emits via `sqlite3.connect(...).execute(...)`. SQLite WAL mode + `INSERT ... ON CONFLICT(signature) DO UPDATE SET count = count + 1` is atomic per-statement — no app-level locking required even when 4 forkserver children fire the same exception within microseconds.
- Parent worker process: typically no jarvis-owned errors there, but installing the handler costs nothing and catches the rare case.
- `logger.exception(...)` calls inside `ErrorTelemetryHandler._emit_impl` would normally trigger recursion (handler logs an error → its own handler fires). The thread-local `_in_emit` guard drops re-entries silently. The `_emit_impl` body is also wrapped in `try/except Exception: pass` as a second layer of defence.

## Part 7 — Window / retention

- **Detection windows:** burst (2h) and drip (7d). Both use `last_seen` against `now` to filter stale signatures. Table itself is unbounded — a signature first seen 6 months ago still lives in the table.
- **Retention (v2 follow-up):** add `vacuum_old_signatures()` that deletes rows where `proposed_at IS NOT NULL AND proposed_at < now-30d` OR `last_seen < now-90d`. Wire into a once-per-day marker file. Not in v1 — table grows ~1KB/signature, won't be a problem before 1000+ unique signatures.
- **Sample traceback storage:** 8 frames max, 4 KB total cap (enforced by `_truncate_tb`). Adequate for human + LLM inspection.

## Part 8 — Risk + intent-payload caps

- **Self-fix prohibition.** `pipeline/automod/` is on the HARD_BLOCKLIST. If a bug in `error_logger.py` or `_scan_errors` raises, the auto-mod loop CANNOT propose a fix for itself. The user must patch it manually. This is by design — preventing a feedback loop where a buggy detector proposes "fixes" that make itself buggier. Implication: this PR's code needs more careful review than typical, because future autonomous self-repair won't bail us out.
- **Intent body cap.** The `intent` field in queue.jsonl is propagated verbatim into the wrapper's `<id>.intent.txt`, then concatenated into the LLM prompt. Cap intent body to 4 KB (sample_traceback ≤ 4 KB + frames list ≤ 10 files + boilerplate ≤ 1 KB ≤ 4 KB total fits). Subagent does its own targeted reads via the CLI's tools.
- **Hot-path overhead.** `ErrorTelemetryHandler.emit()` runs on every ERROR log call across two logger trees. Worst case: an asyncio loop hitting a rapid-fire exception. SQLite INSERT ~1 ms; 1000 errors/s would saturate. Mitigation: errors that bad would already crash JARVIS. Not a v1 concern.
- **Fixability false negatives.** A heuristic-scored "low fixability" exception that's actually fixable would never reach the queue. Mitigation: `JARVIS_AUTOMOD_ERROR_FIXABILITY_FLOOR=0.0` env override allows operator to see all signatures cross-threshold and triage manually.
- **Fixability false positives.** A "high fixability" exception that the LLM can't fix burns a daily-cap slot. Acceptable: 3 PRs/day cap + manual merge filter the noise.
- **Recursive failure mode** addressed: thread-local reentrance guard.
- **Logger attachment surface.** Attaching to `livekit.agents` might capture warnings/errors from upstream code we don't own. The "jarvis-owned frame required" filter ensures only our bugs get signatured. Framework noise drops cleanly.

## Part 9 — Testing

`tests/test_automod_error_logger.py` (new, 6 tests):
1. Handler skips records with `levelno < ERROR`
2. Handler skips records with `exc_info=None`
3. Handler skips exception classes in `_DEFAULT_IGNORE_EXC`
4. Handler skips when no jarvis-owned frame in traceback
5. Handler upserts via `ON CONFLICT`, incrementing count on repeat
6. Handler install is idempotent (re-install doesn't double-attach)

`tests/test_automod_scan_errors.py` (new, 5 tests):
1. `_scan_errors` emits when count ≥ 3 AND `last_seen` ≥ now-2h (burst path)
2. `_scan_errors` emits when count ≥ 10 AND `last_seen` ≥ now-7d (drip path)
3. `_scan_errors` does NOT emit when count < 3
4. `_scan_errors` does NOT emit when `fixability_score < 0.5`
5. `_scan_errors` does NOT re-emit after `proposed_at` is set

`tests/test_automod_error_fallback.py` (new, 4 tests):
1. `populate_from_log_if_empty` no-ops when table has rows
2. `populate_from_log_if_empty` parses JSON-line ERROR records with traceback
3. `populate_from_log_if_empty` skips records outside 24h window
4. `populate_from_log_if_empty` tolerates malformed JSON lines

`tests/test_automod_error_signature_stability.py` (new, 3 tests):
1. Signature is identical for the same `(exc_class, frames_set)` regardless of line number
2. Signature differs between same exc_class at different files (provides disambiguation)
3. Two distinct bugs that share a centralized handler still get distinct signatures because the deeper frames differ

`tests/test_automod_error_fixability.py` (new, 5 tests):
1. `ValueError` with normal message returns ≥ 0.5
2. Exception with "api_key invalid" in message returns < 0.5
3. Exception with top frame in `providers/` returns < 0.5
4. `CancelledError` is dropped before scoring (in `_DEFAULT_IGNORE_EXC`)
5. `JARVIS_AUTOMOD_ERROR_IGNORE_EXC` env var extends the ignore set

`tests/test_recurring_errors_migration.py` (new, 2 tests):
1. `init_db` creates `recurring_errors` table with all required columns
2. `init_db` is idempotent — second call is a no-op (no schema change)

## Part 10 — Verification path

1. All 25 unit tests pass: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_automod_error_*.py tests/test_recurring_errors_migration.py -v`
2. Restart voice-agent. Check log for `[automod] error telemetry handler installed`.
3. Force an exception (e.g., manually break a tool, ask JARVIS something that triggers it 3+ times within 2 hours).
4. Verify the row appears: `sqlite3 ~/.local/share/jarvis/turn_telemetry.db "SELECT signature, exc_class, count, fixability_score FROM recurring_errors"`.
5. Wait for the next scan_and_emit cycle (≤ 1800s) OR force one via the existing automod CLI.
6. Verify intent emits to queue: `cat ~/.jarvis/auto-mods/queue.jsonl | tail -1 | jq .kind` shows `"error"`.
7. If `JARVIS_AUTOMOD_SPAWN_LIVE=1`, the spawner picks it up; otherwise the intent sits in the queue until the flag flips.
8. After a successful subagent run, the proposal lands in `bin/jarvis-automod list`.

## Spec / plan references

- This spec: `docs/superpowers/specs/2026-05-27-automod-error-driven-branch-design.md`
- Auto-mod parent spec: `docs/superpowers/specs/2026-05-24-jarvis-source-code-self-mod-design.md`
- Research synthesis (in-session, not committed): Sentry Seer architecture, Rollbar grouping, SelfHeal paper, AgentFixer paper, test-overfitting empirical work

## What this spec deliberately does NOT do

- **No auto-merge.** Manual `bin/jarvis-automod merge <id>` gate stays.
- **No new tools or LLM-side mechanism.** The wrapper script + spawner already do the LLM work. We're only adding a third source of intents.
- **No real-time alerting.** A spike of errors does not trigger a notification — only enqueues a future proposal.
- **No prompt regression detection.** Voice quality issues are not deterministic exceptions; outside the scope of this branch.
- **No retroactive fix retries.** A failed proposal doesn't auto-retry. Operator decides via `bin/jarvis-automod reject <id>` (or just lets it expire from the table after 30 days in v2).
