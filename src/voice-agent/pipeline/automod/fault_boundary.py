"""Supervised-execution fault boundary for the autonomous evolution loop.

Every entry point a systemd --user timer fires — ``cycle.run_cycle``,
``watchdog.run_once``, ``nightly.run``, ``ondemand.run`` — MUST be crash-proof.
An unhandled exception there is a silently dead process: no record, and for the
watchdog (the deploy safety net) no rollback. This module provides ONE boundary
so that guarantee is *uniform* rather than re-implemented, inconsistently, per
entry point:

  ``@supervised(label, fallback=...)``   wrap a top-level entry point — it can
                                         never propagate; on a crash it records
                                         the fault and returns ``fallback`` (a
                                         degraded but well-formed result).
  ``run_unit(label, fn, on_error=...)``  bulkhead one unit of a batch loop — a
                                         single failure is recorded + degraded,
                                         the batch keeps going.

Every caught fault is RECORDED the same way — structured log + audit record +
``experience_signal.bump`` — so a crash becomes a signal the evolution loop can
pick up and self-fix, not a swallowed error.

Only ``Exception`` is caught; ``SystemExit`` / ``KeyboardInterrupt`` /
``GeneratorExit`` (``BaseException``) always propagate. This is human-owned
safety code on the auto-mod HARD_BLOCKLIST — the loop can never weaken its own
crash guard.
"""
from __future__ import annotations

import functools
import logging
from typing import Any, Callable, TypeVar

logger = logging.getLogger("jarvis.automod.fault_boundary")

T = TypeVar("T")

# Audit event kind emitted for every supervised / bulkheaded fault.
FAULT_EVENT = "automod_supervised_fault"


def record_fault(label: str, exc: BaseException) -> None:
    """Uniform fault recording: structured log + audit trail + a learnable
    signal. Best-effort — recording a fault must never itself raise."""
    logger.exception("[evolution] supervised fault in %s: %s", label, exc)
    detail = f"{type(exc).__name__}: {exc}"[:300]
    try:
        from pipeline.automod import artifact
        artifact.audit(FAULT_EVENT, label=label, error=detail)
    except Exception:  # noqa: BLE001 — telemetry must never break the boundary
        pass
    try:
        from pipeline.automod import experience_signal
        experience_signal.bump(f"crash:{label}")
    except Exception:  # noqa: BLE001
        pass


def supervised(label: str, *, fallback: Any = None
               ) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorate a top-level autonomous entry point so it can NEVER propagate an
    unhandled exception. On a crash: record the fault, return ``fallback`` (a
    value, or a 0-arg callable producing the degraded result)."""
    def deco(fn: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 — that's the entire point
                record_fault(label, exc)
                return fallback() if callable(fallback) else fallback
        return wrapper
    return deco


def run_unit(label: str, fn: Callable[[], T], *, on_error: Any) -> T:
    """Run ONE unit of a batch under the boundary. Returns ``fn()``'s result,
    or — if it raised — records the fault and returns ``on_error`` (a value, or
    a callable taking the exception). A failed unit never aborts the batch."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        record_fault(label, exc)
        return on_error(exc) if callable(on_error) else on_error
