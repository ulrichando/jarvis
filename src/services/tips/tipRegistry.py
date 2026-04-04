"""
Tip registry -- defines all available tips and their relevance criteria.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .tipHistory import get_sessions_since_last_shown


@dataclass
class Tip:
    """A tip that can be shown to the user."""
    id: str
    content: Callable[..., Awaitable[str]]
    cooldown_sessions: int = 10
    is_relevant: Callable[..., Awaitable[bool]] = lambda: True  # type: ignore


@dataclass
class TipContext:
    """Context passed to tip relevance checks."""
    bash_tools: Optional[set] = None
    read_file_state: Optional[dict] = None
    theme: str = "default"


# Built-in tips
_BUILTIN_TIPS: List[Tip] = [
    Tip(
        id="new-user-warmup",
        content=lambda: "Start with small features or bug fixes, tell JARVIS to propose a plan",
        cooldown_sessions=3,
    ),
    Tip(
        id="memory-command",
        content=lambda: "Use /memory to view and manage Claude memory",
        cooldown_sessions=15,
    ),
    Tip(
        id="shift-enter",
        content=lambda: "Press Shift+Enter to send a multi-line message",
        cooldown_sessions=10,
    ),
    Tip(
        id="double-esc",
        content=lambda: "Double-tap esc to rewind the conversation to a previous point",
        cooldown_sessions=10,
    ),
    Tip(
        id="continue",
        content=lambda: "Run jarvis --continue to resume a conversation",
        cooldown_sessions=10,
    ),
    Tip(
        id="custom-commands",
        content=lambda: "Create skills by adding .md files to .jarvis/skills/",
        cooldown_sessions=15,
    ),
    Tip(
        id="permissions",
        content=lambda: "Use /permissions to pre-approve and pre-deny tools",
        cooldown_sessions=10,
    ),
    Tip(
        id="todo-list",
        content=lambda: "Ask to create a todo list when working on complex tasks",
        cooldown_sessions=20,
    ),
    Tip(
        id="feedback-command",
        content=lambda: "Use /feedback to help us improve!",
        cooldown_sessions=15,
    ),
]


async def get_relevant_tips(context: Optional[TipContext] = None) -> List[Tip]:
    """Get all currently relevant tips."""
    relevant = []
    for tip in _BUILTIN_TIPS:
        sessions_since = get_sessions_since_last_shown(tip.id)
        if sessions_since >= tip.cooldown_sessions:
            relevant.append(tip)
    return relevant
