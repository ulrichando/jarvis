"""Phase 3 — contract for the supervised-execution fault boundary.

The PERMANENT fix for "an autonomous entry point can crash": one reusable
boundary that guarantees no unhandled exception ever propagates out of a
timer-fired entry point (run_cycle / watchdog.run_once / nightly / ondemand),
that every fault is recorded (audit + a learnable experience signal so the loop
can self-fix), and that a single failed unit never aborts a batch.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_VOICE_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_VOICE_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_VOICE_AGENT_ROOT))

from pipeline.automod import fault_boundary  # noqa: E402


def _raise(msg: str):
    raise RuntimeError(msg)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    return tmp_path


# ── supervised: the top-level entry-point guard ──────────────────────────────

def test_supervised_passes_through_normal_return(home):
    @fault_boundary.supervised("demo", fallback="FALLBACK")
    def ok():
        return "real-result"
    assert ok() == "real-result"


def test_supervised_returns_fallback_on_exception(home):
    @fault_boundary.supervised("demo", fallback="FALLBACK")
    def boom():
        raise RuntimeError("kaboom")
    assert boom() == "FALLBACK"          # caught — did NOT propagate


def test_supervised_fallback_can_be_callable(home):
    @fault_boundary.supervised("demo", fallback=lambda: {"crashed": True})
    def boom():
        raise ValueError("x")
    assert boom() == {"crashed": True}


@pytest.mark.parametrize("exc", [SystemExit, KeyboardInterrupt])
def test_supervised_never_swallows_exit_signals(home, exc):
    @fault_boundary.supervised("demo", fallback="FALLBACK")
    def boom():
        raise exc()
    with pytest.raises(exc):
        boom()


# ── run_unit: the per-unit bulkhead inside a batch ───────────────────────────

def test_run_unit_returns_result_normally(home):
    assert fault_boundary.run_unit("u", lambda: 42, on_error=-1) == 42


def test_run_unit_returns_on_error_value_on_exception(home):
    out = fault_boundary.run_unit("u", lambda: _raise("x"), on_error="DEGRADED")
    assert out == "DEGRADED"


def test_run_unit_on_error_can_be_callable_receiving_exc(home):
    out = fault_boundary.run_unit("u", lambda: _raise("boom-msg"),
                                  on_error=lambda e: f"err:{e}")
    assert out == "err:boom-msg"


def test_run_unit_never_swallows_exit_signals(home):
    with pytest.raises(SystemExit):
        fault_boundary.run_unit("u", lambda: (_ for _ in ()).throw(SystemExit()),
                                on_error="never")


# ── every caught fault is observable + learnable ─────────────────────────────

def test_caught_fault_is_recorded_and_signals_learning(home, monkeypatch):
    from pipeline.automod import artifact, experience_signal
    experience_signal.clear()
    audits: list[tuple] = []
    monkeypatch.setattr(artifact, "audit", lambda kind, **f: audits.append((kind, f)))

    @fault_boundary.supervised("nightly", fallback=None)
    def boom():
        raise RuntimeError("disk full")
    boom()

    # learnable: the experience signal fired so the loop can self-fix its crash
    assert experience_signal.is_set() is True
    # observable: an audit record names the failing entry point
    assert any(f.get("label") == "nightly" for _k, f in audits), audits


def test_run_unit_fault_is_also_recorded(home, monkeypatch):
    from pipeline.automod import artifact, experience_signal
    experience_signal.clear()
    audits: list[tuple] = []
    monkeypatch.setattr(artifact, "audit", lambda kind, **f: audits.append((kind, f)))

    fault_boundary.run_unit("build:x", lambda: _raise("oom"), on_error=None)

    assert experience_signal.is_set() is True
    assert any(f.get("label") == "build:x" for _k, f in audits), audits
