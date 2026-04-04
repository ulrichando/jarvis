"""
Python equivalent of ids.ts

Branded/NewType IDs for session and agent identification.
"""

from __future__ import annotations

import re
from typing import NewType, Optional

# Branded types via NewType -- prevents accidental mixing at static analysis time.

SessionId = NewType("SessionId", str)
"""A session ID uniquely identifies a JARVIS session."""

AgentId = NewType("AgentId", str)
"""An agent ID uniquely identifies a subagent within a session."""


def as_session_id(id_: str) -> SessionId:
    """Cast a raw string to SessionId. Use sparingly -- prefer getSessionId() when possible."""
    return SessionId(id_)


def as_agent_id(id_: str) -> AgentId:
    """Cast a raw string to AgentId. Use sparingly -- prefer createAgentId() when possible."""
    return AgentId(id_)


_AGENT_ID_PATTERN = re.compile(r"^a(?:.+-)?[0-9a-f]{16}$")


def to_agent_id(s: str) -> Optional[AgentId]:
    """
    Validate and brand a string as AgentId.
    Matches the format produced by createAgentId(): 'a' + optional '<label>-' + 16 hex chars.
    Returns None if the string doesn't match.
    """
    if _AGENT_ID_PATTERN.match(s):
        return AgentId(s)
    return None
