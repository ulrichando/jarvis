"""useApp hook - access the App context."""
from __future__ import annotations
from typing import Callable
from ..components.app_context import AppContext


def use_app() -> AppContext:
    """Access the application context. Returns exit function."""
    return AppContext()
