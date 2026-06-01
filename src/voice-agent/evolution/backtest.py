"""Back-test harness — window scoring + comparison over real telemetry.

Thin composition over the read-only reader + pure signal/fitness functions.
No I/O of its own beyond the read-only telemetry read; no import-time side
effects. This is the calibration anchor: known-bad windows must score below
known-good ones, or the fitness function is wrong and nothing may depend on it.
"""
from __future__ import annotations
from pathlib import Path
from typing import Optional
from . import db_read, signals as _sig, fitness as _fit


def score_window(db_path: Path = db_read.DEFAULT_TELEMETRY_DB, *, since: Optional[str] = None,
                 until: Optional[str] = None) -> _fit.FitnessReading:
    return _fit.score(_sig.compute_signals(db_read.read_turns(db_path, since=since, until=until)))


def compare_windows(windows: list[tuple], db_path: Path = db_read.DEFAULT_TELEMETRY_DB) -> list[dict]:
    """windows: [(label, since, until)] → [{label, composite, passed, per_axis, n_turns}]."""
    out = []
    for label, since, until in windows:
        r = score_window(db_path, since=since, until=until)
        out.append({"label": label, "composite": r.composite, "passed": r.passed,
                    "per_axis": r.per_axis, "n_turns": r.n_turns})
    return out
