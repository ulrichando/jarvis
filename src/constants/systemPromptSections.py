"""System prompt section management with caching."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, List, Optional, Union

# Module-level cache for prompt sections
_section_cache: Dict[str, Optional[str]] = {}

ComputeFn = Callable[[], Union[Optional[str], Awaitable[Optional[str]]]]


@dataclass
class SystemPromptSection:
    name: str
    compute: ComputeFn
    cache_break: bool


def system_prompt_section(name: str, compute: ComputeFn) -> SystemPromptSection:
    """Create a memoized system prompt section.
    Computed once, cached until /clear or /compact.
    """
    return SystemPromptSection(name=name, compute=compute, cache_break=False)


def dangerous_uncached_system_prompt_section(
    name: str,
    compute: ComputeFn,
    reason: str,
) -> SystemPromptSection:
    """Create a volatile system prompt section that recomputes every turn.
    This WILL break the prompt cache when the value changes.
    """
    return SystemPromptSection(name=name, compute=compute, cache_break=True)


async def resolve_system_prompt_sections(
    sections: List[SystemPromptSection],
) -> List[Optional[str]]:
    """Resolve all system prompt sections, returning prompt strings."""
    results: List[Optional[str]] = []

    for s in sections:
        if not s.cache_break and s.name in _section_cache:
            results.append(_section_cache.get(s.name))
            continue

        value = s.compute()
        if asyncio.iscoroutine(value):
            value = await value

        _section_cache[s.name] = value
        results.append(value)

    return results


def clear_system_prompt_sections() -> None:
    """Clear all system prompt section state."""
    _section_cache.clear()
