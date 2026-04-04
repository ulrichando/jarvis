"""Exit command implementation."""

from __future__ import annotations

import sys
from typing import Any


async def call(on_done: Any = None, **_kwargs: Any) -> None:
    """Exit the REPL."""
    if on_done:
        on_done("Goodbye!", {"display": "system"})
    sys.exit(0)
