"""
Turn-scoped workload tag via contextvars.

Uses contextvars (Python equivalent of AsyncLocalStorage) to provide
isolated workload context that survives across await boundaries.
"""

import contextvars
from typing import Callable, Literal, Optional, TypeVar

T = TypeVar("T")

Workload = Literal["cron"]
WORKLOAD_CRON: Workload = "cron"

_workload_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "workload", default=None
)


def get_workload() -> Optional[str]:
    """Get the current workload tag, or None if not set."""
    return _workload_var.get()


def run_with_workload(workload: Optional[str], fn: Callable[[], T]) -> T:
    """
    Wrap fn in a workload context. ALWAYS establishes a new context boundary,
    even when workload is None.

    This guarantees get_workload() inside fn returns exactly what the caller
    passed, including None.
    """
    token = _workload_var.set(workload)
    try:
        return fn()
    finally:
        _workload_var.reset(token)
