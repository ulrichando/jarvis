"""
Current working directory management with async context support.
"""

from __future__ import annotations

import contextvars
import os
from typing import Callable, TypeVar

T = TypeVar("T")

_cwd_override: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "cwd_override", default=None
)

_original_cwd: str = os.getcwd()


def run_with_cwd_override(cwd: str, fn: Callable[[], T]) -> T:
    """
    Run a function with an overridden working directory for the current context.
    All calls to pwd()/get_cwd() within the function will return the overridden cwd.
    """
    token = _cwd_override.set(cwd)
    try:
        return fn()
    finally:
        _cwd_override.reset(token)


def pwd() -> str:
    """Get the current working directory."""
    override = _cwd_override.get()
    if override is not None:
        return override
    return os.getcwd()


def get_cwd() -> str:
    """Get the current working directory, falling back to the original cwd."""
    try:
        return pwd()
    except Exception:
        return _original_cwd
