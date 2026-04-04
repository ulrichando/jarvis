"""useStdin hook - access stdin context."""
from __future__ import annotations
from ..components.stdin_context import StdinContext

def use_stdin() -> StdinContext:
    return StdinContext()
