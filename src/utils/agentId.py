"""
Deterministic Agent ID System.

Provides helper functions for formatting and parsing deterministic
agent IDs used in the swarm/teammate system.

ID Formats:
  Agent IDs: agentName@teamName
  Request IDs: {requestType}-{timestamp}@{agentId}
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional


def format_agent_id(agent_name: str, team_name: str) -> str:
    """Formats an agent ID in the format agentName@teamName."""
    return f"{agent_name}@{team_name}"


@dataclass
class ParsedAgentId:
    agent_name: str
    team_name: str


def parse_agent_id(agent_id: str) -> Optional[ParsedAgentId]:
    """
    Parses an agent ID into its components.
    Returns None if the ID doesn't contain the @ separator.
    """
    at_index = agent_id.find("@")
    if at_index == -1:
        return None
    return ParsedAgentId(
        agent_name=agent_id[:at_index],
        team_name=agent_id[at_index + 1 :],
    )


def generate_request_id(request_type: str, agent_id: str) -> str:
    """Formats a request ID in the format {requestType}-{timestamp}@{agentId}."""
    timestamp = int(time.time() * 1000)
    return f"{request_type}-{timestamp}@{agent_id}"


@dataclass
class ParsedRequestId:
    request_type: str
    timestamp: int
    agent_id: str


def parse_request_id(request_id: str) -> Optional[ParsedRequestId]:
    """
    Parses a request ID into its components.
    Returns None if the request ID doesn't match the expected format.
    """
    at_index = request_id.find("@")
    if at_index == -1:
        return None

    prefix = request_id[:at_index]
    agent_id = request_id[at_index + 1 :]

    last_dash_index = prefix.rfind("-")
    if last_dash_index == -1:
        return None

    request_type = prefix[:last_dash_index]
    timestamp_str = prefix[last_dash_index + 1 :]

    try:
        timestamp = int(timestamp_str)
    except ValueError:
        return None

    return ParsedRequestId(
        request_type=request_type,
        timestamp=timestamp,
        agent_id=agent_id,
    )
