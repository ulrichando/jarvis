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
