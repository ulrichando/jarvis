"""HandoffSubagent + registry — pure-Python, zero LiveKit imports.

Each subagent is a `HandoffSubagent` dataclass. The supervisor reads
the registry at agent startup and generates one `transfer_to_X`
function_tool per registered, enabled spec.

The registry is global module state. That's fine for JARVIS (one
agent process per session) and matches how `@function_tool` already
works (decorator side-effects).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional


__all__ = [
    # Handoff-style sub-agents (sticky multi-turn, `transfer_to_X`)
    "HandoffSubagent",
    "register",
    "all_specs",
    "get",
    "clear",
    # Delegated-style sub-agents (one-shot, `delegate(role, task)`)
    "DelegatedSubagent",
    "register_subagent",
    "all_subagents",
    "get_subagent",
    "clear_subagents",
    # Internal registries (exposed for tests; not for direct mutation)
    "_REGISTRY",
    "SUBAGENT_REGISTRY",
]


@dataclass
class HandoffSubagent:
    """Declarative spec for a subagent sub-agent.

    Fields:
        name: short identifier — `desktop`, `browser`, `planner`, …
        transfer_tool: the function_tool name the supervisor exposes
                       (e.g. `transfer_to_desktop`). Convention:
                       `transfer_to_{name}`.
        when_to_use: one-line description — populates the function_tool
                     docstring the LLM reads when picking a route.
        instructions: the subagent's system prompt. Keep tight (~100
                      lines) so tool-call discipline stays sharp.
        tool_factory: zero-arg callable returning the list of @function_tool
                      objects the subagent gets. Lazy because LiveKit
                      decorators have to run inside the agent process,
                      not at registry import time.
        ack_phrase: brief voiced ack when the supervisor hands off to
                    this subagent. Defaults to "Right away."
        max_history_items: number of recent chat_ctx items carried into
                           the subagent on handoff. None = no truncation.
        enabled: gate for hot-disabling without unregistering.
        llm_factory: optional zero-arg callable returning an LLM or
                     RealtimeModel for this subagent. When None
                     (default), the subagent inherits the supervisor's
                     LLM. Use this to route a subagent through a
                     different provider — e.g. screen_share uses
                     Gemini Live (RealtimeModel) for sub-second vision
                     while the supervisor stays on Claude Haiku +
                     Groq Orpheus.
        tools_required: when True (default), the subagent tool-gate
                     refuses `task_done` if no real (non-task_done)
                     tool fired this handoff. Set False for tool-less
                     subagents like screen_share whose RealtimeModel
                     produces the work directly (audio + transcription
                     stream) without going through function_tool calls.
                     The gate's purpose is to catch confabulating
                     LLMs that bail before acting — irrelevant when
                     there's nothing to act with.
    """
    name: str
    transfer_tool: str
    when_to_use: str
    instructions: str
    tool_factory: Callable[[], list]
    ack_phrase: str = "Right away."
    max_history_items: Optional[int] = 12
    enabled: bool = True
    llm_factory: Optional[Callable[[], object]] = None
    tools_required: bool = True


# Global registry. Use the module functions below — don't mutate this
# dict directly (the helpers handle name collisions and re-registration
# semantics).
_REGISTRY: dict[str, HandoffSubagent] = {}


def register(spec: HandoffSubagent) -> None:
    """Register a subagent. Re-registering the same name overwrites —
    intentional, lets a subagent module be re-imported without errors
    (test reloads, hot-reloads). Logs/warns the caller if it cares."""
    if not spec.name:
        raise ValueError("HandoffSubagent.name must be non-empty")
    if not spec.transfer_tool:
        raise ValueError("HandoffSubagent.transfer_tool must be non-empty")
    _REGISTRY[spec.name] = spec


def all_specs() -> list[HandoffSubagent]:
    """All enabled specs, in registration order. Disabled specs are
    skipped here so the supervisor only sees what the user wants."""
    return [s for s in _REGISTRY.values() if s.enabled]


def get(name: str) -> Optional[HandoffSubagent]:
    """Lookup by name. None if missing OR disabled (matches all_specs
    semantics — callers shouldn't get a disabled spec by accident)."""
    s = _REGISTRY.get(name)
    return s if s and s.enabled else None


def clear() -> None:
    """Reset the registry. Test-only — production agents register on
    import and never clear."""
    _REGISTRY.clear()


# ── DelegatedSubagent — same shape, different routing ─────────────────────
#
# HandoffSubagent → supervisor exposes ONE `transfer_to_{name}` tool per
# spec. Doesn't scale past ~25 specs (each tool def costs ~300 prompt
# tokens; supervisor TTFW grows linearly with N).
#
# DelegatedSubagent → supervisor exposes a single `delegate(role, task)` tool
# that covers ALL registered subagents. Token cost is constant in N.
# Use for new subagents where prompt-bloat matters more than per-tool
# specificity. The 3 existing subagents (planner / desktop / browser)
# stay on `HandoffSubagent` for back-compat.


@dataclass
class DelegatedSubagent:
    """Declarative spec for a sub-agent reachable via `delegate(role, task)`.

    Same fields as HandoffSubagent minus `transfer_tool` (there is no
    per-spec transfer tool — `delegate` handles routing by name).
    """
    name: str
    when_to_use: str
    instructions: str
    tool_factory: Callable[[], list]
    ack_phrase: str = "Right away."
    max_history_items: Optional[int] = 12
    enabled: bool = True


SUBAGENT_REGISTRY: dict[str, DelegatedSubagent] = {}


def register_subagent(spec: DelegatedSubagent) -> None:
    """Register a subagent. Re-registering the same name overwrites,
    matching `register()` semantics for HandoffSubagent."""
    if not spec.name:
        raise ValueError("DelegatedSubagent.name must be non-empty")
    SUBAGENT_REGISTRY[spec.name] = spec


def all_subagents() -> list[DelegatedSubagent]:
    """All enabled subagents in registration order."""
    return [s for s in SUBAGENT_REGISTRY.values() if s.enabled]


def get_subagent(name: str) -> Optional[DelegatedSubagent]:
    """Lookup by name. None if missing OR disabled."""
    s = SUBAGENT_REGISTRY.get(name)
    return s if s and s.enabled else None


def clear_subagents() -> None:
    """Reset the subagent registry. Test-only."""
    SUBAGENT_REGISTRY.clear()
