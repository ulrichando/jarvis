"""Query loop - iterative tool-calling loop for agent interactions.

The query loop sends messages+tools to the LLM, executes returned tool_calls,
appends results, and repeats until the LLM returns no tool_calls or hits
the iteration limit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import (
    Any,
    AsyncGenerator,
    Callable,
    Dict,
    List,
    Optional,
    Union,
)

from src.tools.Tool import ToolUseContext, Tools


@dataclass
class QueryParams:
    """Parameters for a query loop invocation."""
    messages: List[Any] = field(default_factory=list)
    system_prompt: Any = None
    user_context: Dict[str, str] = field(default_factory=dict)
    system_context: Dict[str, str] = field(default_factory=dict)
    can_use_tool: Optional[Callable] = None
    tool_use_context: Optional[ToolUseContext] = None
    fallback_model: Optional[str] = None
    query_source: str = ""
    max_output_tokens_override: Optional[int] = None
    max_turns: Optional[int] = None
    skip_cache_write: bool = False
    task_budget: Optional[Dict[str, float]] = None


@dataclass
class Terminal:
    """Terminal state - query loop has finished."""
    reason: str = ""
    messages: List[Any] = field(default_factory=list)


@dataclass
class Continue:
    """Continue state - query loop should continue."""
    reason: str = ""


MAX_OUTPUT_TOKENS_RECOVERY_LIMIT = 3


async def query(
    params: QueryParams,
) -> AsyncGenerator[Any, None]:
    """Run the query loop.

    Yields stream events, messages, and tombstones.
    Returns a Terminal when done.

    In the TypeScript version, this is a complex generator that handles:
    - Tool execution and result accumulation
    - Auto-compaction when context gets large
    - Max output tokens recovery
    - Stop hooks
    - Budget tracking
    - Snip boundaries

    This Python version provides the type structure; the actual implementation
    uses Brain.think() and agent_loop() in the JARVIS architecture.
    """
    # Stub implementation
    return
    yield  # Make this a generator  # noqa: E501
