# Auto-mod Error-Driven Branch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the existing JARVIS auto-mod loop with a third detection scanner that catches recurring exceptions from JARVIS's own code and emits proposal intents the same way the existing correction + confab scanners do.

**Architecture:** A `logging.Handler` subclass attached to the `jarvis` + `livekit.agents` root loggers writes ERROR-level records with `exc_info` to a new SQLite table (`recurring_errors`) via atomic `INSERT ... ON CONFLICT(signature) DO UPDATE`. A direct-tail of `voice-agent.log` exists as a cold-start fallback. A new `_scan_errors()` function in `pipeline/automod/patterns.py` reads the table, applies a dual-threshold gate (burst 3-in-2h OR drip 10-in-7d) plus a cheap fixability score (skip provider-auth/transient errors), and emits intent records to the existing `queue.jsonl`. No changes to the spawner / finalize / merge CLI / blocklist / wrapper script — the queue contract is the integration boundary.

**Tech Stack:** Python 3.13 stdlib, SQLite (via existing `turn_telemetry.db`), pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-05-27-automod-error-driven-branch-design.md`

---

## File structure

| File | Responsibility | New/Modified |
|---|---|---|
| `src/voice-agent/pipeline/turn_telemetry.py` | `init_db()` gains an idempotent `CREATE TABLE recurring_errors` + 1 index | Modified |
| `src/voice-agent/pipeline/automod/error_logger.py` | All capture-side logic: signature, fixability, ignore-set, `_upsert`, `ErrorTelemetryHandler`, `install_error_handler` | Created |
| `src/voice-agent/pipeline/automod/error_log_fallback.py` | Direct-tail of `voice-agent.log`; `populate_from_log_if_empty(conn)` | Created |
| `src/voice-agent/pipeline/automod/patterns.py` | Add `_scan_errors(conn)` + module constants; one new line in `scan_and_emit()` | Modified |
| `src/voice-agent/jarvis_agent.py` | Add one try/except block at session-start that calls `install_error_handler()` | Modified |
| `src/voice-agent/tests/test_recurring_errors_migration.py` | Idempotent CREATE TABLE + column existence | Created |
| `src/voice-agent/tests/test_automod_error_signature_stability.py` | Signature scheme tests (no line-number sensitivity, multi-frame disambiguation) | Created |
| `src/voice-agent/tests/test_automod_error_fixability.py` | Fixability scorer + ignore-set env override | Created |
| `src/voice-agent/tests/test_automod_error_logger.py` | Handler behavior + idempotent install | Created |
| `src/voice-agent/tests/test_automod_error_fallback.py` | Direct-tail fallback parsing + cutoff + JSON tolerance | Created |
| `src/voice-agent/tests/test_automod_scan_errors.py` | Scanner dual-threshold + fixability filter + dedup | Created |

7 implementation tasks below + 1 smoke-test task.

---

## Task 1: Add `recurring_errors` table + migration test

**Files:**
- Modify: `src/voice-agent/pipeline/turn_telemetry.py` (extend `_BASE_SCHEMA` or add to `init_db`)
- Test: `src/voice-agent/tests/test_recurring_errors_migration.py`

- [ ] **Step 1: Write the failing test**

Create `src/voice-agent/tests/test_recurring_errors_migration.py`:

```python
"""Tests for the recurring_errors table — created by init_db() for the
auto-mod error-driven branch (Spec 2026-05-27)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from pipeline.turn_telemetry import init_db


def test_init_db_creates_recurring_errors_table(tmp_path):
    db_path = tmp_path / "telemetry.db"
    init_db(db_path)
    with sqlite3.connect(db_path) as c:
        rows = c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='recurring_errors'"
        ).fetchall()
        assert rows == [("recurring_errors",)]


def test_recurring_errors_has_all_required_columns(tmp_path):
    db_path = tmp_path / "telemetry.db"
    init_db(db_path)
    with sqlite3.connect(db_path) as c:
        cols = {row[1] for row in c.execute(
            "PRAGMA table_info(recurring_errors)"
        ).fetchall()}
    required = {
        "signature", "exc_class", "exc_message",
        "first_seen", "last_seen", "count",
        "frames_json", "sample_traceback",
        "fixability_score", "proposed_at",
    }
    missing = required - cols
    assert not missing, f"missing columns: {missing}"


def test_init_db_is_idempotent_for_recurring_errors(tmp_path):
    db_path = tmp_path / "telemetry.db"
    init_db(db_path)
    # Insert one row to verify it survives a second init_db call.
    with sqlite3.connect(db_path) as c:
        c.execute("""
            INSERT INTO recurring_errors
                (signature, exc_class, first_seen, last_seen, count, frames_json)
            VALUES (?, ?, ?, ?, ?, ?)
        """, ("abc123", "ValueError",
              "2026-05-27T00:00:00Z", "2026-05-27T00:00:00Z",
              1, "[]"))
    # Second init_db should NOT drop the table or the row.
    init_db(db_path)
    with sqlite3.connect(db_path) as c:
        rows = c.execute(
            "SELECT signature FROM recurring_errors"
        ).fetchall()
    assert rows == [("abc123",)], "row should survive idempotent init"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_recurring_errors_migration.py -v`

Expected: FAIL with `sqlite3.OperationalError: no such table: recurring_errors`.

- [ ] **Step 3: Add the schema to init_db**

In `src/voice-agent/pipeline/turn_telemetry.py`, locate `init_db(db_path)` (function should be near the top, after `_BASE_SCHEMA`). It already runs the base schema; append a new `CREATE TABLE IF NOT EXISTS` and one index. Add this code at the END of `init_db(db_path)`, just before its `return` (or as the last statement in the function body):

```python
    # Auto-mod error-driven branch (Spec 2026-05-27). Idempotent.
    # Populated by pipeline/automod/error_logger.ErrorTelemetryHandler;
    # read by pipeline/automod/patterns._scan_errors.
    with sqlite3.connect(str(db_path)) as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS recurring_errors (
                signature TEXT PRIMARY KEY,
                exc_class TEXT NOT NULL,
                exc_message TEXT,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 1,
                frames_json TEXT NOT NULL,
                sample_traceback TEXT,
                fixability_score REAL DEFAULT 0.5,
                proposed_at TEXT
            )
        """)
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_recurring_errors_last_seen
                ON recurring_errors(last_seen)
        """)
```

NOTE: if `init_db` uses a different SQLite connection pattern (e.g., a single `with sqlite3.connect(...) as c:` block at the top), append the two `c.execute(...)` statements inside that existing block instead of opening a new connection. Read the function first; match its style.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_recurring_errors_migration.py -v`

Expected: ALL 3 tests PASS.

Also run the existing telemetry suite to ensure no regressions:

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_turn_telemetry.py -v 2>&1 | tail -10`

Expected: ALL pass (same baseline).

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/pipeline/turn_telemetry.py src/voice-agent/tests/test_recurring_errors_migration.py
git commit -m "telemetry: add recurring_errors table for auto-mod error branch"
```

---

## Task 2: Add helper functions in error_logger.py — signature, fixability, ignore-set

**Files:**
- Create: `src/voice-agent/pipeline/automod/error_logger.py` (helpers only, no class yet)
- Test: `src/voice-agent/tests/test_automod_error_signature_stability.py`
- Test: `src/voice-agent/tests/test_automod_error_fixability.py`

- [ ] **Step 1: Write the failing signature-stability tests**

Create `src/voice-agent/tests/test_automod_error_signature_stability.py`:

```python
"""Tests for the signature scheme — must be stable across unrelated
line-number changes, and must disambiguate distinct bugs that share
a centralized handler. Spec 2026-05-27 Part 2."""
from __future__ import annotations

import pytest


def test_signature_excludes_line_numbers():
    """Same exc_class, same (file, method), different line numbers
    must produce the SAME signature."""
    from pipeline.automod.error_logger import _signature
    frames_a = [("src/voice-agent/jarvis_agent.py", "_on_user_input")]
    frames_b = [("src/voice-agent/jarvis_agent.py", "_on_user_input")]
    # Line numbers are not in the input to _signature at all — proving
    # the API doesn't accept them is a stronger guarantee than asserting
    # they're stripped.
    assert _signature("ValueError", frames_a) == _signature("ValueError", frames_b)


def test_signature_differs_for_different_exc_class():
    """Different exc_class → different signature, even with identical frames."""
    from pipeline.automod.error_logger import _signature
    frames = [("src/voice-agent/jarvis_agent.py", "_on_user_input")]
    assert _signature("ValueError", frames) != _signature("KeyError", frames)


def test_signature_differs_for_different_files():
    """Same exc_class at different files → different signatures."""
    from pipeline.automod.error_logger import _signature
    frames_a = [("src/voice-agent/jarvis_agent.py", "foo")]
    frames_b = [("src/voice-agent/pipeline/turn_router.py", "foo")]
    assert _signature("ValueError", frames_a) != _signature("ValueError", frames_b)


def test_multi_frame_disambiguates_centralized_handler():
    """Two distinct bugs both surfacing through a shared centralized
    handler must get DIFFERENT signatures because the deeper frames
    differ. This is the multi-frame value-add over single-frame schemes."""
    from pipeline.automod.error_logger import _signature
    # Bug A: deep call from foo(), bubbles through central_handler()
    frames_a = [
        ("src/voice-agent/tools/foo.py", "do_foo_thing"),
        ("src/voice-agent/jarvis_agent.py", "central_handler"),
    ]
    # Bug B: deep call from bar(), bubbles through the same handler
    frames_b = [
        ("src/voice-agent/tools/bar.py", "do_bar_thing"),
        ("src/voice-agent/jarvis_agent.py", "central_handler"),
    ]
    sig_a = _signature("ValueError", frames_a)
    sig_b = _signature("ValueError", frames_b)
    assert sig_a != sig_b, (
        "multi-frame signature must distinguish bugs that share a "
        "centralized handler"
    )


def test_signature_is_order_independent():
    """Frame ORDER should not affect the signature — same set of (file,
    method) pairs in different orders must produce identical sigs.
    (Spec calls for sorted(set(...)) inside the signature function.)"""
    from pipeline.automod.error_logger import _signature
    frames_a = [
        ("src/voice-agent/tools/foo.py", "do_foo"),
        ("src/voice-agent/jarvis_agent.py", "handler"),
    ]
    frames_b = [
        ("src/voice-agent/jarvis_agent.py", "handler"),
        ("src/voice-agent/tools/foo.py", "do_foo"),
    ]
    assert _signature("ValueError", frames_a) == _signature("ValueError", frames_b)
```

- [ ] **Step 2: Write the failing fixability tests**

Create `src/voice-agent/tests/test_automod_error_fixability.py`:

```python
"""Tests for the fixability heuristic — caller emits intent only if
score >= 0.5. Spec 2026-05-27 Part 2."""
from __future__ import annotations

import os
import unittest.mock as mock

import pytest


def test_value_error_with_normal_message_is_fixable():
    from pipeline.automod.error_logger import _fixability_score
    frames = [("src/voice-agent/jarvis_agent.py", "foo")]
    score = _fixability_score("ValueError", "got 3, expected 2", frames)
    assert score >= 0.5


def test_auth_error_message_drops_below_floor():
    from pipeline.automod.error_logger import _fixability_score
    frames = [("src/voice-agent/jarvis_agent.py", "foo")]
    score = _fixability_score("RuntimeError",
                              "Anthropic returned 401: invalid api_key",
                              frames)
    assert score < 0.5, f"auth error should not be fixable, got {score}"


def test_provider_frame_drops_score():
    """Top jarvis frame in providers/ → typically transient/external."""
    from pipeline.automod.error_logger import _fixability_score
    frames = [("src/voice-agent/providers/llm.py", "build_dispatching_llm")]
    score = _fixability_score("ConnectionError", "connect: 502", frames)
    assert score < 0.5


def test_resilience_frame_drops_score():
    from pipeline.automod.error_logger import _fixability_score
    frames = [("src/voice-agent/resilience/circuit_breaker.py", "trip")]
    score = _fixability_score("RuntimeError", "breaker open", frames)
    assert score < 0.5


def test_high_fixability_class_with_no_low_signals_scores_well():
    """ValidationError + no auth/rate-limit hints + frame not in
    providers/ → comfortably above floor."""
    from pipeline.automod.error_logger import _fixability_score
    frames = [("src/voice-agent/tools/dispatch_agent.py", "handle_dispatch_agent")]
    score = _fixability_score("ValidationError", "field foo missing", frames)
    assert score >= 0.7  # 0.5 baseline + 0.3 for high-fixability class


def test_ignore_set_default_membership():
    """The default ignore set includes lifecycle exceptions we never fix."""
    from pipeline.automod.error_logger import _ignore_set
    s = _ignore_set()
    assert "CancelledError" in s
    assert "KeyboardInterrupt" in s
    assert "SystemExit" in s


def test_ignore_set_env_override_extends_default():
    """JARVIS_AUTOMOD_ERROR_IGNORE_EXC adds to (not replaces) the default."""
    from pipeline.automod.error_logger import _ignore_set
    with mock.patch.dict(os.environ,
                         {"JARVIS_AUTOMOD_ERROR_IGNORE_EXC": "MyCustomExc,AnotherExc"}):
        s = _ignore_set()
    assert "MyCustomExc" in s
    assert "AnotherExc" in s
    assert "CancelledError" in s  # default still present
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_automod_error_signature_stability.py tests/test_automod_error_fixability.py -v 2>&1 | tail -10`

Expected: ALL tests FAIL with `ModuleNotFoundError: No module named 'pipeline.automod.error_logger'`.

- [ ] **Step 4: Create error_logger.py with helper functions only (no class yet)**

Create `src/voice-agent/pipeline/automod/error_logger.py`:

```python
"""Logging handler that captures recurring exceptions for the auto-mod
error-driven branch (Spec 2026-05-27).

This module is import-safe — no side effects at import time. The
handler is installed at session-start via `install_error_handler()`."""
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_automod_error_signature_stability.py tests/test_automod_error_fixability.py -v`

Expected: ALL 12 tests PASS (5 signature + 7 fixability).

- [ ] **Step 6: Commit**

```bash
git add src/voice-agent/pipeline/automod/error_logger.py src/voice-agent/tests/test_automod_error_signature_stability.py src/voice-agent/tests/test_automod_error_fixability.py
git commit -m "automod: add error_logger helpers (signature, fixability, ignore-set, upsert)"
```

---

## Task 3: Add `ErrorTelemetryHandler` class + `install_error_handler`

**Files:**
- Modify: `src/voice-agent/pipeline/automod/error_logger.py` (extend, add class)
- Test: `src/voice-agent/tests/test_automod_error_logger.py`

- [ ] **Step 1: Write the failing tests**

Create `src/voice-agent/tests/test_automod_error_logger.py`:

```python
"""Tests for ErrorTelemetryHandler + install_error_handler.
Spec 2026-05-27 Part 2."""
from __future__ import annotations

import logging
import os
import sqlite3
import sys
import unittest.mock as mock
from pathlib import Path

import pytest


@pytest.fixture
def telemetry_db(tmp_path, monkeypatch):
    """Provide an isolated telemetry DB with the recurring_errors table."""
    db_path = tmp_path / "telemetry.db"
    monkeypatch.setenv("JARVIS_TURN_TELEMETRY_DB", str(db_path))
    from pipeline.turn_telemetry import init_db
    init_db(db_path)
    yield db_path


def _make_jarvis_traceback():
    """Construct a real traceback that includes a jarvis-owned frame.
    We synthesize a stack via a fake file path."""
    try:
        # The frame this raises in IS this test file (tests/),
        # which is in _VENDOR_HINTS — gets filtered. Construct a
        # synthetic traceback with a real jarvis-owned frame by
        # raising from inside an exec() block whose filename we
        # control.
        code = compile(
            "raise ValueError('test error')",
            "/home/ulrich/Documents/Projects/jarvis/src/voice-agent/synth_frame.py",
            "exec",
        )
        exec(code, {})
    except ValueError:
        return sys.exc_info()


def _make_log_record(exc_info) -> logging.LogRecord:
    return logging.LogRecord(
        name="jarvis",
        level=logging.ERROR,
        pathname="anywhere.py",
        lineno=1,
        msg="boom",
        args=(),
        exc_info=exc_info,
    )


def test_emit_skips_records_below_error_level(telemetry_db):
    from pipeline.automod.error_logger import ErrorTelemetryHandler
    h = ErrorTelemetryHandler(level=logging.WARNING)
    rec = logging.LogRecord("jarvis", logging.WARNING, "x.py", 1,
                            "warn", (), None)
    h.emit(rec)
    with sqlite3.connect(telemetry_db) as c:
        n = c.execute("SELECT COUNT(*) FROM recurring_errors").fetchone()[0]
    assert n == 0


def test_emit_skips_records_with_no_exc_info(telemetry_db):
    """logger.error('oops') without exc_info — diagnostic message,
    skip (no exception to signature)."""
    from pipeline.automod.error_logger import ErrorTelemetryHandler
    h = ErrorTelemetryHandler(level=logging.ERROR)
    rec = logging.LogRecord("jarvis", logging.ERROR, "x.py", 1,
                            "oops", (), None)
    h.emit(rec)
    with sqlite3.connect(telemetry_db) as c:
        n = c.execute("SELECT COUNT(*) FROM recurring_errors").fetchone()[0]
    assert n == 0


def test_emit_skips_ignored_exception_class(telemetry_db):
    from pipeline.automod.error_logger import ErrorTelemetryHandler
    h = ErrorTelemetryHandler(level=logging.ERROR)
    try:
        raise KeyboardInterrupt("user hit Ctrl-C")
    except KeyboardInterrupt:
        exc_info = sys.exc_info()
    h.emit(_make_log_record(exc_info))
    with sqlite3.connect(telemetry_db) as c:
        n = c.execute("SELECT COUNT(*) FROM recurring_errors").fetchone()[0]
    assert n == 0


def test_emit_skips_when_no_jarvis_frame(telemetry_db):
    """Pure-stdlib traceback (no src/voice-agent/ frame) → skip."""
    from pipeline.automod.error_logger import ErrorTelemetryHandler
    h = ErrorTelemetryHandler(level=logging.ERROR)
    try:
        # Raise from a real stdlib path — no jarvis frame in the tb.
        {}.missing_key  # AttributeError raised from this very test file.
    except AttributeError:
        exc_info = sys.exc_info()
    # The test file is under tests/ which is in _VENDOR_HINTS → filtered.
    h.emit(_make_log_record(exc_info))
    with sqlite3.connect(telemetry_db) as c:
        n = c.execute("SELECT COUNT(*) FROM recurring_errors").fetchone()[0]
    assert n == 0


def test_emit_upserts_on_repeat_incrementing_count(telemetry_db):
    """Same signature emitted twice → 1 row, count=2."""
    from pipeline.automod.error_logger import ErrorTelemetryHandler
    h = ErrorTelemetryHandler(level=logging.ERROR)
    exc_info = _make_jarvis_traceback()
    h.emit(_make_log_record(exc_info))
    h.emit(_make_log_record(exc_info))
    with sqlite3.connect(telemetry_db) as c:
        rows = c.execute(
            "SELECT signature, count FROM recurring_errors"
        ).fetchall()
    assert len(rows) == 1, f"expected 1 row, got {len(rows)}: {rows}"
    assert rows[0][1] == 2, f"count should be 2, got {rows[0][1]}"


def test_install_error_handler_is_idempotent(telemetry_db):
    """Re-installing must not double-attach handlers to the same logger."""
    from pipeline.automod.error_logger import (
        install_error_handler, ErrorTelemetryHandler,
    )
    # Reset module state in case earlier tests installed.
    import pipeline.automod.error_logger as mod
    mod._INSTALLED_HANDLER = None
    # Remove any handlers that prior install left on the loggers.
    for name in mod._ATTACH_LOGGERS:
        target = logging.getLogger(name)
        for h in list(target.handlers):
            if isinstance(h, ErrorTelemetryHandler):
                target.removeHandler(h)

    install_error_handler()
    install_error_handler()
    install_error_handler()

    jarvis_logger = logging.getLogger("jarvis")
    instances = [h for h in jarvis_logger.handlers
                 if isinstance(h, ErrorTelemetryHandler)]
    assert len(instances) == 1, (
        f"expected 1 handler attached after 3 installs, got {len(instances)}"
    )


def test_emit_reentrance_guard_prevents_recursion(telemetry_db):
    """If emit() raises and the framework logs that exception,
    re-entry must be a no-op (else recursion loop)."""
    from pipeline.automod.error_logger import ErrorTelemetryHandler
    import pipeline.automod.error_logger as mod
    h = ErrorTelemetryHandler(level=logging.ERROR)
    # Force _in_emit.active=True to simulate reentry mid-emit.
    mod._in_emit.active = True
    try:
        exc_info = _make_jarvis_traceback()
        h.emit(_make_log_record(exc_info))  # should be a no-op
        with sqlite3.connect(telemetry_db) as c:
            n = c.execute(
                "SELECT COUNT(*) FROM recurring_errors"
            ).fetchone()[0]
        assert n == 0, "reentrance guard should drop the record"
    finally:
        mod._in_emit.active = False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_automod_error_logger.py -v`

Expected: FAIL with `ImportError: cannot import name 'ErrorTelemetryHandler'`.

- [ ] **Step 3: Add the handler class + install function**

Append to `src/voice-agent/pipeline/automod/error_logger.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_automod_error_logger.py -v`

Expected: ALL 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/pipeline/automod/error_logger.py src/voice-agent/tests/test_automod_error_logger.py
git commit -m "automod: add ErrorTelemetryHandler + install_error_handler"
```

---

## Task 4: Add `error_log_fallback.py` (direct-tail seed)

**Files:**
- Create: `src/voice-agent/pipeline/automod/error_log_fallback.py`
- Test: `src/voice-agent/tests/test_automod_error_fallback.py`

- [ ] **Step 1: Write the failing tests**

Create `src/voice-agent/tests/test_automod_error_fallback.py`:

```python
"""Tests for the direct-tail fallback that seeds recurring_errors from
voice-agent.log when the handler hasn't populated it yet.
Spec 2026-05-27 Part 4."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest


@pytest.fixture
def telemetry_db(tmp_path, monkeypatch):
    db_path = tmp_path / "telemetry.db"
    monkeypatch.setenv("JARVIS_TURN_TELEMETRY_DB", str(db_path))
    from pipeline.turn_telemetry import init_db
    init_db(db_path)
    yield db_path


@pytest.fixture
def fake_log(tmp_path, monkeypatch):
    """Monkeypatch LOG_PATH so the fallback reads from tmp."""
    p = tmp_path / "voice-agent.log"
    monkeypatch.setattr(
        "pipeline.automod.error_log_fallback.LOG_PATH", p,
    )
    return p


def _json_log_line(level: str, traceback_text: str | None,
                   ts_offset_sec: float = 0.0) -> str:
    """Build one JSON-line log record with the given level + tb."""
    ts_iso = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ",
        time.gmtime(time.time() + ts_offset_sec),
    )
    rec = {"level": level, "message": "x", "timestamp": ts_iso}
    if traceback_text:
        rec["exception"] = traceback_text
    return json.dumps(rec) + "\n"


_SAMPLE_TB = (
    'Traceback (most recent call last):\n'
    '  File "/home/ulrich/Documents/Projects/jarvis/src/voice-agent/jarvis_agent.py", line 42, in handle\n'
    '    raise ValueError("bad input")\n'
    'ValueError: bad input\n'
)


def test_no_op_when_table_has_rows(telemetry_db, fake_log):
    """Populates only when the table is empty. If a handler already
    inserted rows, the fallback must NOT touch anything."""
    fake_log.write_text(_json_log_line("ERROR", _SAMPLE_TB), encoding="utf-8")
    with sqlite3.connect(telemetry_db) as c:
        c.execute("""INSERT INTO recurring_errors
                     (signature, exc_class, first_seen, last_seen, count,
                      frames_json)
                     VALUES (?, ?, ?, ?, ?, ?)""",
                  ("preexisting", "RuntimeError",
                   "2026-05-27T00:00:00Z", "2026-05-27T00:00:00Z",
                   5, "[]"))
    from pipeline.automod.error_log_fallback import populate_from_log_if_empty
    with sqlite3.connect(telemetry_db) as c:
        inserted = populate_from_log_if_empty(c)
    assert inserted == 0
    with sqlite3.connect(telemetry_db) as c:
        rows = c.execute("SELECT signature FROM recurring_errors").fetchall()
    assert ("preexisting",) in rows
    assert len(rows) == 1, "no new rows inserted"


def test_parses_json_error_records_with_traceback(telemetry_db, fake_log):
    """A single ERROR record with a jarvis-owned traceback → 1 row."""
    fake_log.write_text(_json_log_line("ERROR", _SAMPLE_TB), encoding="utf-8")
    from pipeline.automod.error_log_fallback import populate_from_log_if_empty
    with sqlite3.connect(telemetry_db) as c:
        inserted = populate_from_log_if_empty(c)
    assert inserted == 1
    with sqlite3.connect(telemetry_db) as c:
        rows = c.execute(
            "SELECT exc_class, count FROM recurring_errors"
        ).fetchall()
    assert rows == [("ValueError", 1)]


def test_skips_records_outside_24h_window(telemetry_db, fake_log):
    """Records with timestamp older than 24h must be ignored."""
    old = _json_log_line("ERROR", _SAMPLE_TB,
                         ts_offset_sec=-25 * 3600)  # 25h ago
    fresh = _json_log_line("ERROR", _SAMPLE_TB,
                           ts_offset_sec=-1 * 3600)  # 1h ago
    fake_log.write_text(old + fresh, encoding="utf-8")
    from pipeline.automod.error_log_fallback import populate_from_log_if_empty
    with sqlite3.connect(telemetry_db) as c:
        inserted = populate_from_log_if_empty(c)
    # Same signature for both records → 1 row, count incremented only
    # for the in-window record.
    assert inserted == 1
    with sqlite3.connect(telemetry_db) as c:
        rows = c.execute(
            "SELECT count FROM recurring_errors"
        ).fetchall()
    assert rows == [(1,)], (
        f"only the fresh record should have been ingested; got {rows}"
    )


def test_tolerates_malformed_json_lines(telemetry_db, fake_log):
    """Garbled lines must not abort the whole scan."""
    body = (
        "not-json-at-all\n"
        "{broken json\n"
        + _json_log_line("ERROR", _SAMPLE_TB)
        + "\n"  # blank line
        + _json_log_line("INFO", None)  # non-error
    )
    fake_log.write_text(body, encoding="utf-8")
    from pipeline.automod.error_log_fallback import populate_from_log_if_empty
    with sqlite3.connect(telemetry_db) as c:
        inserted = populate_from_log_if_empty(c)
    assert inserted == 1


def test_no_log_file_no_op(telemetry_db, tmp_path, monkeypatch):
    """If voice-agent.log doesn't exist, return 0 cleanly."""
    monkeypatch.setattr(
        "pipeline.automod.error_log_fallback.LOG_PATH",
        tmp_path / "does-not-exist.log",
    )
    from pipeline.automod.error_log_fallback import populate_from_log_if_empty
    with sqlite3.connect(telemetry_db) as c:
        inserted = populate_from_log_if_empty(c)
    assert inserted == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_automod_error_fallback.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline.automod.error_log_fallback'`.

- [ ] **Step 3: Create the fallback module**

Create `src/voice-agent/pipeline/automod/error_log_fallback.py`:

```python
"""Fallback population of recurring_errors from voice-agent.log.

Used when the ErrorTelemetryHandler hasn't been wired yet (fresh
session, table is empty) but the log file already has 24h of
exception records we shouldn't ignore. Best-effort; failures
return 0 silently.

Spec 2026-05-27 Part 4."""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from pipeline.automod.error_logger import (
    _is_jarvis_owned,
    _signature,
    _fixability_score,
    _upsert,
    _ignore_set,
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
    voice-agent.log. Returns count of new records ingested (each ingest
    may upsert into the same signature, so this counts events not unique
    signatures).

    Always-safe: returns 0 on any failure."""
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
    ingested = 0
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
                ingested += 1
    except OSError:
        return 0

    if ingested:
        logger.info("[automod] fallback ingested %d log records", ingested)
    return ingested


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
        # The exception class is the last "ExcClass: message" line.
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

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_automod_error_fallback.py -v`

Expected: ALL 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/pipeline/automod/error_log_fallback.py src/voice-agent/tests/test_automod_error_fallback.py
git commit -m "automod: add error_log_fallback for cold-start population"
```

---

## Task 5: Add `_scan_errors` to `patterns.py`

**Files:**
- Modify: `src/voice-agent/pipeline/automod/patterns.py`
- Test: `src/voice-agent/tests/test_automod_scan_errors.py`

- [ ] **Step 1: Write the failing tests**

Create `src/voice-agent/tests/test_automod_scan_errors.py`:

```python
"""Tests for _scan_errors — dual-threshold gate + fixability filter +
dedup via proposed_at. Spec 2026-05-27 Part 3."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest


@pytest.fixture
def telemetry_db(tmp_path, monkeypatch):
    db_path = tmp_path / "telemetry.db"
    monkeypatch.setenv("JARVIS_TURN_TELEMETRY_DB", str(db_path))
    from pipeline.turn_telemetry import init_db
    init_db(db_path)
    yield db_path


@pytest.fixture
def queue_dir(tmp_path, monkeypatch):
    """Isolate ~/.jarvis/auto-mods to tmp."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "jarvis-home"))
    (tmp_path / "jarvis-home" / "auto-mods").mkdir(parents=True, exist_ok=True)
    yield tmp_path / "jarvis-home" / "auto-mods"


def _iso(seconds_ago: float = 0.0) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ",
                         time.gmtime(time.time() - seconds_ago))


def _insert_error(conn, *, sig, exc_class="ValueError", count=1,
                  last_seen_sec_ago=0, fixability=0.8, proposed_at=None):
    conn.execute("""
        INSERT INTO recurring_errors
            (signature, exc_class, exc_message, first_seen, last_seen,
             count, frames_json, sample_traceback, fixability_score,
             proposed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (sig, exc_class, "msg",
          _iso(seconds_ago=last_seen_sec_ago + 1),
          _iso(seconds_ago=last_seen_sec_ago),
          count, json.dumps([{"file": "src/voice-agent/jarvis_agent.py",
                              "method": "foo"}]),
          "tb", fixability, proposed_at))
    conn.commit()


def test_emits_when_burst_threshold_met(telemetry_db, queue_dir):
    """count >= 3 AND last_seen within 2h → emit."""
    with sqlite3.connect(telemetry_db) as c:
        _insert_error(c, sig="burst1", count=3, last_seen_sec_ago=600)
    from pipeline.automod.patterns import _scan_errors
    with sqlite3.connect(telemetry_db) as c:
        emitted = _scan_errors(c)
    assert emitted == 1
    queue_file = queue_dir / "queue.jsonl"
    assert queue_file.exists()
    lines = queue_file.read_text().strip().splitlines()
    rec = json.loads(lines[-1])
    assert rec["kind"] == "error"
    assert "ValueError" in rec["intent"]


def test_emits_when_drip_threshold_met(telemetry_db, queue_dir):
    """count >= 10 AND last_seen within 7d but not 2h → still emit (drip)."""
    with sqlite3.connect(telemetry_db) as c:
        _insert_error(c, sig="drip1", count=10,
                      last_seen_sec_ago=3 * 86400)  # 3 days ago
    from pipeline.automod.patterns import _scan_errors
    with sqlite3.connect(telemetry_db) as c:
        emitted = _scan_errors(c)
    assert emitted == 1


def test_does_not_emit_below_burst_count(telemetry_db, queue_dir):
    """count=2 in 2h window → below burst threshold of 3."""
    with sqlite3.connect(telemetry_db) as c:
        _insert_error(c, sig="x", count=2, last_seen_sec_ago=600)
    from pipeline.automod.patterns import _scan_errors
    with sqlite3.connect(telemetry_db) as c:
        emitted = _scan_errors(c)
    assert emitted == 0


def test_does_not_emit_when_fixability_below_floor(telemetry_db, queue_dir):
    """fixability_score < 0.5 → never emit, regardless of count."""
    with sqlite3.connect(telemetry_db) as c:
        _insert_error(c, sig="lowfix", count=20, last_seen_sec_ago=600,
                      fixability=0.4)
    from pipeline.automod.patterns import _scan_errors
    with sqlite3.connect(telemetry_db) as c:
        emitted = _scan_errors(c)
    assert emitted == 0


def test_does_not_re_emit_after_proposed_at_set(telemetry_db, queue_dir):
    """proposed_at IS NOT NULL → already proposed, skip."""
    with sqlite3.connect(telemetry_db) as c:
        _insert_error(c, sig="seen", count=5, last_seen_sec_ago=600,
                      proposed_at=_iso(seconds_ago=60))
    from pipeline.automod.patterns import _scan_errors
    with sqlite3.connect(telemetry_db) as c:
        emitted = _scan_errors(c)
    assert emitted == 0


def test_scan_sets_proposed_at_on_emit(telemetry_db, queue_dir):
    """After emit, proposed_at must be populated so the next scan skips."""
    with sqlite3.connect(telemetry_db) as c:
        _insert_error(c, sig="emit_me", count=3, last_seen_sec_ago=600)
    from pipeline.automod.patterns import _scan_errors
    with sqlite3.connect(telemetry_db) as c:
        _scan_errors(c)
    with sqlite3.connect(telemetry_db) as c:
        row = c.execute(
            "SELECT proposed_at FROM recurring_errors WHERE signature=?",
            ("emit_me",),
        ).fetchone()
    assert row[0] is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_automod_scan_errors.py -v`

Expected: FAIL with `ImportError: cannot import name '_scan_errors'`.

- [ ] **Step 3: Add `_scan_errors` to patterns.py**

In `src/voice-agent/pipeline/automod/patterns.py`, add the following imports + constants + function. The imports go near the top (after existing imports), the constants near the existing `THRESHOLD = 3` line, and the function before `scan_and_emit()`:

```python
# Added imports — append to existing import block at top:
import json
```

(If `json` is already imported, skip this addition.)

Add these constants near the existing `THRESHOLD = 3` line:

```python
ERROR_BURST_WINDOW_HOURS = 2
ERROR_BURST_COUNT = 3
ERROR_DRIP_WINDOW_DAYS = 7
ERROR_DRIP_COUNT = 10
ERROR_FIXABILITY_FLOOR = 0.5
```

Add this function BEFORE the existing `scan_and_emit()`:

```python
def _iso_offset(seconds_delta: int) -> str:
    return time.strftime(
        "%Y-%m-%dT%H:%M:%SZ",
        time.gmtime(time.time() + seconds_delta),
    )


def _scan_errors(conn: sqlite3.Connection) -> int:
    """Emit intents for recurring errors that crossed either threshold.

    Burst path: count >= ERROR_BURST_COUNT AND last_seen within
                ERROR_BURST_WINDOW_HOURS hours.
    Drip path:  count >= ERROR_DRIP_COUNT AND last_seen within
                ERROR_DRIP_WINDOW_DAYS days.

    Both gated on fixability_score >= ERROR_FIXABILITY_FLOOR and
    proposed_at IS NULL.

    Spec 2026-05-27 Part 3."""
    # Fallback: if the handler hasn't wired yet, seed from log.
    try:
        from pipeline.automod.error_log_fallback import populate_from_log_if_empty
        populate_from_log_if_empty(conn)
    except Exception as _e:
        logger.debug("[automod] fallback skipped: %s", _e)

    burst_cutoff = _iso_offset(-ERROR_BURST_WINDOW_HOURS * 3600)
    drip_cutoff = _iso_offset(-ERROR_DRIP_WINDOW_DAYS * 86400)

    try:
        rows = conn.execute("""
            SELECT signature, exc_class, exc_message, count,
                   first_seen, last_seen, frames_json, sample_traceback,
                   fixability_score
              FROM recurring_errors
             WHERE proposed_at IS NULL
               AND fixability_score >= ?
               AND (
                    (count >= ? AND last_seen >= ?)
                 OR (count >= ? AND last_seen >= ?)
               )
             ORDER BY count DESC, last_seen DESC
        """, (ERROR_FIXABILITY_FLOOR,
              ERROR_BURST_COUNT, burst_cutoff,
              ERROR_DRIP_COUNT, drip_cutoff)).fetchall()
    except sqlite3.Error as e:
        logger.warning("[automod] _scan_errors query failed: %s", e)
        return 0

    emitted = 0
    for (sig, exc_class, exc_msg, count, first, last,
         frames_json, sample_tb, fixability) in rows:
        rec_id = _next_id("error")
        try:
            frames = json.loads(frames_json or "[]")
        except json.JSONDecodeError:
            frames = []
        frames_text = "\n".join(
            f"  - {f.get('file', '?')}:{f.get('method', '?')}"
            for f in frames
        )
        intent_body = (
            f"Investigate a recurring exception in JARVIS's own code.\n\n"
            f"EXCEPTION: {exc_class}\n"
            f"MESSAGE:   {exc_msg!r}\n"
            f"OCCURRENCES: {count} "
            f"(first seen {first}, last seen {last})\n"
            f"FIXABILITY: {fixability:.2f}\n\n"
            f"AFFECTED FILES (jarvis-owned frames in the traceback):\n"
            f"{frames_text}\n\n"
            f"SAMPLE TRACEBACK:\n"
            f"{sample_tb}\n\n"
            f"INVESTIGATE: read each affected file, identify the root "
            f"cause (may be at any frame in the stack, not just the top), "
            f"and propose a targeted fix. The fix should either prevent "
            f"the exception from being raised OR handle it cleanly when "
            f"it cannot be prevented. Do NOT add a broad except: that "
            f"hides the underlying bug."
        )
        _emit({
            "id": rec_id,
            "kind": "error",
            "intent": intent_body,
            "rationale": (
                f"raised {count} times ({first} → {last}); "
                f"fixability={fixability:.2f}"
            ),
            "evidence": {
                "signature": sig, "exc_class": exc_class,
                "exc_message": exc_msg, "count": count,
                "first_seen": first, "last_seen": last,
                "frames": frames, "fixability_score": fixability,
            },
            "created_at": _now_iso(),
        })
        try:
            conn.execute(
                "UPDATE recurring_errors SET proposed_at=? WHERE signature=?",
                (_now_iso(), sig),
            )
        except sqlite3.Error as e:
            logger.warning("[automod] proposed_at update failed: %s", e)
        emitted += 1
    conn.commit()
    return emitted
```

Then add the new scanner call to `scan_and_emit()`. The existing function ends with:

```python
def scan_and_emit() -> int:
    ...
    try:
        with sqlite3.connect(str(db)) as conn:
            emitted += _scan_corrections(conn)
            emitted += _scan_confabs(conn)
    except sqlite3.Error as e:
        ...
```

Change the inner block to:

```python
        with sqlite3.connect(str(db)) as conn:
            emitted += _scan_corrections(conn)
            emitted += _scan_confabs(conn)
            emitted += _scan_errors(conn)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_automod_scan_errors.py -v`

Expected: ALL 6 tests PASS.

Also run the existing automod tests to confirm no regressions:

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/ -v -k "automod" 2>&1 | tail -10`

Expected: All existing automod tests still pass.

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/pipeline/automod/patterns.py src/voice-agent/tests/test_automod_scan_errors.py
git commit -m "automod: add _scan_errors with dual-threshold gate (burst + drip)"
```

---

## Task 6: Wire `install_error_handler` into `jarvis_agent.py`

**Files:**
- Modify: `src/voice-agent/jarvis_agent.py`

- [ ] **Step 1: Locate the existing `init_db` call**

Use grep to find the line:

```bash
grep -n "init_db(DEFAULT_DB_PATH)" /home/ulrich/Documents/Projects/jarvis/src/voice-agent/jarvis_agent.py
```

It should be around line 5327, inside the agent entrypoint, inside a `try/except` block that already handles `init_db` failure with a log warning.

Read 10 lines around it to see the surrounding context:

```bash
sed -n '5320,5340p' /home/ulrich/Documents/Projects/jarvis/src/voice-agent/jarvis_agent.py
```

- [ ] **Step 2: Add the install call**

In `src/voice-agent/jarvis_agent.py`, IMMEDIATELY AFTER the existing `try/except` block that calls `init_db(DEFAULT_DB_PATH)` (around line 5326-5329), insert a new block:

```python
    # Install the auto-mod error telemetry handler. Captures recurring
    # exceptions from this process for the auto-mod error-driven scanner
    # to detect. Idempotent — re-install is a no-op. The handler reads
    # the same telemetry DB that init_db() just initialized.
    # Spec: docs/superpowers/specs/2026-05-27-automod-error-driven-branch-design.md
    try:
        from pipeline.automod.error_logger import install_error_handler
        install_error_handler()
    except Exception as _e:
        logger.warning(f"[automod] error handler install failed: {_e}")
```

DO NOT touch anything else in `jarvis_agent.py`. Only this insertion.

- [ ] **Step 3: Verify the agent still imports cleanly**

Run: `cd src/voice-agent && .venv/bin/python -c "import jarvis_agent; print('IMPORT OK')"`

Expected output (after the standard sanitizer-install lines):

```
IMPORT OK
```

- [ ] **Step 4: Run the full automod test suite for regression check**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_automod_error_logger.py tests/test_automod_error_signature_stability.py tests/test_automod_error_fixability.py tests/test_automod_error_fallback.py tests/test_automod_scan_errors.py tests/test_recurring_errors_migration.py -v`

Expected: ALL 25 new tests pass.

Also run a broader sanity check:

```
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent && .venv/bin/python -m pytest tests/ --ignore=tests/test_memory_injection_no_bump.py -q 2>&1 | tail -10
```

Expected: full suite passes (or same baseline — the only known pre-existing skip is `test_memory_injection_no_bump.py`).

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/jarvis_agent.py
git commit -m "automod: install error telemetry handler at session-start"
```

---

## Task 7: Manual smoke test against live voice-agent

This task verifies the end-to-end flow against a running JARVIS. Mandatory before considering the feature shipped.

- [ ] **Step 1: Check telemetry DB before restart**

Run: `sqlite3 ~/.local/share/jarvis/turn_telemetry.db "SELECT ts_utc FROM turns ORDER BY id DESC LIMIT 1"`

If the latest `ts_utc` is within 60s, ASK THE USER before restarting (per the load-bearing rule in `CLAUDE.md`). Otherwise proceed.

- [ ] **Step 2: Restart the voice-agent**

Run: `systemctl --user restart jarvis-voice-agent.service`

- [ ] **Step 3: Verify the handler installed**

Run: `tail -100 ~/.local/share/jarvis/logs/voice-agent.log | grep "error telemetry handler installed"`

Expected: ONE line per forkserver process, similar to:

```
{"message": "[automod] error telemetry handler installed on jarvis, livekit.agents", ...}
```

If zero lines: the install failed silently. Check `tail -300 ~/.local/share/jarvis/logs/voice-agent.log | grep -E "ERROR|automod"`.

- [ ] **Step 4: Verify the table exists**

Run:
```bash
sqlite3 ~/.local/share/jarvis/turn_telemetry.db \
  "SELECT sql FROM sqlite_master WHERE name='recurring_errors'"
```

Expected: the CREATE TABLE statement printed.

- [ ] **Step 5: Force an exception**

The cleanest way is to make a tool call that raises in our code. Two options:

**Option A — force a known-good code path that raises:**

```bash
# This invokes a debug helper that deliberately raises ValueError for testing.
# If no such helper exists in the codebase, use Option B.
```

**Option B — natural trigger:**

Ask JARVIS by voice: *"Jarvis, run terminal: cat /nonexistent/file"*. The terminal tool should surface a FileNotFoundError that gets logged at ERROR level with traceback. Repeat 3 times.

After 3 repetitions, query the table:

```bash
sqlite3 ~/.local/share/jarvis/turn_telemetry.db \
  "SELECT signature, exc_class, count, fixability_score, last_seen
     FROM recurring_errors ORDER BY last_seen DESC LIMIT 5"
```

Expected: at least one row with `count >= 3`.

- [ ] **Step 6: Wait for or trigger a scan**

The scanner runs on the existing schedule (`scan_and_emit` cadence, default 1800s). For an immediate test, find the existing scheduler call in `jarvis_agent.py` (search for `scan_and_emit`) and verify it's wired. If it runs on a 30-minute cadence and you don't want to wait, manually trigger:

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent && \
  .venv/bin/python -c "from pipeline.automod.patterns import scan_and_emit; print('emitted:', scan_and_emit())"
```

Expected: `emitted: 1` (or higher, if other scanners also crossed threshold).

- [ ] **Step 7: Verify the queue record**

Run:

```bash
tail -1 ~/.jarvis/auto-mods/queue.jsonl | python3 -m json.tool
```

Expected: JSON record with `"kind": "error"`, intent containing `INVESTIGATE`, evidence with the signature.

- [ ] **Step 8: If SPAWN_LIVE=1, verify a proposal appears**

If `JARVIS_AUTOMOD_SPAWN_LIVE=1` is set in the unit file, the spawner will fork the wrapper. Wait 2-3 minutes, then:

```bash
bin/jarvis-automod list
```

Expected: a proposal entry corresponding to the queued intent.

If `SPAWN_LIVE` is NOT set: the queue record sits inert. That's correct behavior in shadow mode.

- [ ] **Step 9: Completion check**

If steps 3-7 all passed, mark this feature as live. The auto-mod error-driven branch is operational.

If `SPAWN_LIVE=1` and step 8 also passed, the full loop is live: errors → queue → spawned proposals → manual merge.

---

## Verification checklist (Spec coverage)

Each spec part maps to a task:

- Spec Part 1 (Storage / SQL schema) → Task 1
- Spec Part 2 (Capture path: handler + helpers) → Tasks 2 + 3
- Spec Part 3 (Scanner extension) → Task 5
- Spec Part 4 (Direct-tail fallback) → Task 4
- Spec Part 5 (Wiring) → Task 6
- Spec Part 6 (Forkserver semantics) → discussion only — implementation covered by Tasks 2-3 (reentrance guard, atomic ON CONFLICT)
- Spec Part 7 (Window / retention) → constants land in Task 5; v2 vacuum follow-up explicitly out of scope
- Spec Part 8 (Risk + intent-payload caps) → safety net covered by reentrance guard (Task 3) + `_truncate_tb` (Task 2) + intent_body assembly (Task 5)
- Spec Part 9 (Testing — 25 tests across 6 files) → distributed across Tasks 1-5
- Spec Part 10 (Verification path) → Task 7
