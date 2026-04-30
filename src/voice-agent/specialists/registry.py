"""SpecialistSpec + registry — pure-Python, zero LiveKit imports.

Each specialist is a `SpecialistSpec` dataclass. The supervisor reads
the registry at agent startup and generates one `transfer_to_X`
function_tool per registered, enabled spec.

The registry is global module state. That's fine for JARVIS (one
agent process per session) and matches how `@function_tool` already
works (decorator side-effects).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class SpecialistSpec:
    """Declarative spec for a specialist sub-agent.

    Fields:
        name: short identifier — `desktop`, `browser`, `planner`, …
        transfer_tool: the function_tool name the supervisor exposes
                       (e.g. `transfer_to_desktop`). Convention:
                       `transfer_to_{name}`.
        when_to_use: one-line description — populates the function_tool
                     docstring the LLM reads when picking a route.
        instructions: the specialist's system prompt. Keep tight (~100
                      lines) so tool-call discipline stays sharp.
        tool_factory: zero-arg callable returning the list of @function_tool
                      objects the specialist gets. Lazy because LiveKit
                      decorators have to run inside the agent process,
                      not at registry import time.
        ack_phrase: brief voiced ack when the supervisor hands off to
                    this specialist. Defaults to "On it, sir."
        max_history_items: number of recent chat_ctx items carried into
                           the specialist on handoff. None = no truncation.
        enabled: gate for hot-disabling without unregistering.
    """
    name: str
    transfer_tool: str
    when_to_use: str
    instructions: str
    tool_factory: Callable[[], list]
    ack_phrase: str = "On it, sir."
    max_history_items: Optional[int] = 12
    enabled: bool = True


# Global registry. Use the module functions below — don't mutate this
# dict directly (the helpers handle name collisions and re-registration
# semantics).
_REGISTRY: dict[str, SpecialistSpec] = {}


def register(spec: SpecialistSpec) -> None:
    """Register a specialist. Re-registering the same name overwrites —
    intentional, lets a specialist module be re-imported without errors
    (test reloads, hot-reloads). Logs/warns the caller if it cares."""
    if not spec.name:
        raise ValueError("SpecialistSpec.name must be non-empty")
    if not spec.transfer_tool:
        raise ValueError("SpecialistSpec.transfer_tool must be non-empty")
    _REGISTRY[spec.name] = spec


def all_specs() -> list[SpecialistSpec]:
    """All enabled specs, in registration order. Disabled specs are
    skipped here so the supervisor only sees what the user wants."""
    return [s for s in _REGISTRY.values() if s.enabled]


def get(name: str) -> Optional[SpecialistSpec]:
    """Lookup by name. None if missing OR disabled (matches all_specs
    semantics — callers shouldn't get a disabled spec by accident)."""
    s = _REGISTRY.get(name)
    return s if s and s.enabled else None


def clear() -> None:
    """Reset the registry. Test-only — production agents register on
    import and never clear."""
    _REGISTRY.clear()
