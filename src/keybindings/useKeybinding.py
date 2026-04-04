"""React hook equivalent for keybinding resolution (Python logic only)."""

from __future__ import annotations

from typing import Callable, Optional

from .resolver import KeybindingResolver
from .types import ParsedKeystroke

_resolver: Optional[KeybindingResolver] = None


def get_resolver() -> KeybindingResolver:
    """Get or create the global keybinding resolver."""
    global _resolver
    if _resolver is None:
        from .loadUserBindings import load_user_bindings
        _resolver = KeybindingResolver(load_user_bindings())
    return _resolver


def resolve_keybinding(context: str, event: ParsedKeystroke) -> Optional[str]:
    """Resolve a keystroke event to an action in the given context."""
    return get_resolver().resolve(context, event)
