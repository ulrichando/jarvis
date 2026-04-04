"""Unified suggestion system combining file, MCP resource, and agent suggestions."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .file_suggestions import SuggestionItem, generate_file_suggestions


@dataclass
class FileSuggestionSource:
    type: str = "file"
    display_text: str = ""
    description: Optional[str] = None
    path: str = ""
    filename: str = ""
    score: Optional[float] = None


@dataclass
class McpResourceSuggestionSource:
    type: str = "mcp_resource"
    display_text: str = ""
    description: str = ""
    server: str = ""
    uri: str = ""
    name: str = ""


@dataclass
class AgentSuggestionSource:
    type: str = "agent"
    display_text: str = ""
    description: str = ""
    agent_type: str = ""
    color: Optional[str] = None


SuggestionSource = FileSuggestionSource | McpResourceSuggestionSource | AgentSuggestionSource

MAX_UNIFIED_SUGGESTIONS = 15
DESCRIPTION_MAX_LENGTH = 60


def truncate_description(description: str) -> str:
    """Truncate description to max length."""
    if len(description) <= DESCRIPTION_MAX_LENGTH:
        return description
    return description[: DESCRIPTION_MAX_LENGTH - 1] + "\u2026"


def create_suggestion_from_source(source: SuggestionSource) -> SuggestionItem:
    """Create a unified suggestion item from a source."""
    if isinstance(source, FileSuggestionSource):
        return SuggestionItem(
            id=f"file-{source.path}",
            display_text=source.display_text,
            description=source.description,
        )
    elif isinstance(source, McpResourceSuggestionSource):
        return SuggestionItem(
            id=f"mcp-resource-{source.server}__{source.uri}",
            display_text=source.display_text,
            description=source.description,
        )
    elif isinstance(source, AgentSuggestionSource):
        return SuggestionItem(
            id=f"agent-{source.agent_type}",
            display_text=source.display_text,
            description=source.description,
            color=source.color,
        )
    raise ValueError(f"Unknown source type: {type(source)}")


@dataclass
class AgentDefinition:
    agent_type: str
    when_to_use: str


def generate_agent_suggestions(
    agents: List[AgentDefinition],
    query: str,
    show_on_empty: bool = False,
) -> List[AgentSuggestionSource]:
    """Generate agent suggestions filtered by query."""
    if not query and not show_on_empty:
        return []

    agent_sources = [
        AgentSuggestionSource(
            display_text=f"{agent.agent_type} (agent)",
            description=truncate_description(agent.when_to_use),
            agent_type=agent.agent_type,
        )
        for agent in agents
    ]

    if not query:
        return agent_sources

    query_lower = query.lower()
    return [
        a
        for a in agent_sources
        if query_lower in a.agent_type.lower()
        or query_lower in a.display_text.lower()
    ]


@dataclass
class ServerResource:
    server: str
    uri: str
    name: Optional[str] = None
    description: Optional[str] = None


async def generate_unified_suggestions(
    query: str,
    cwd: str,
    mcp_resources: Dict[str, List[ServerResource]],
    agents: List[AgentDefinition],
    show_on_empty: bool = False,
) -> List[SuggestionItem]:
    """Generate unified suggestions combining files, MCP resources, and agents.

    Args:
        query: Search query string.
        cwd: Current working directory.
        mcp_resources: Dict of server name to list of resources.
        agents: List of agent definitions.
        show_on_empty: Whether to show suggestions when query is empty.

    Returns:
        Sorted list of suggestion items.
    """
    if not query and not show_on_empty:
        return []

    file_suggestions = await generate_file_suggestions(query, cwd, show_on_empty)
    agent_sources = generate_agent_suggestions(agents, query, show_on_empty)

    file_sources = [
        FileSuggestionSource(
            display_text=s.display_text,
            description=s.description,
            path=s.display_text,
            filename=os.path.basename(s.display_text),
            score=(s.metadata or {}).get("score"),
        )
        for s in file_suggestions
    ]

    mcp_sources = [
        McpResourceSuggestionSource(
            display_text=f"{resource.server}:{resource.uri}",
            description=truncate_description(
                resource.description or resource.name or resource.uri
            ),
            server=resource.server,
            uri=resource.uri,
            name=resource.name or resource.uri,
        )
        for resources in mcp_resources.values()
        for resource in resources
    ]

    if not query:
        all_sources: List[SuggestionSource] = [
            *file_sources,
            *mcp_sources,
            *agent_sources,
        ]
        return [
            create_suggestion_from_source(s)
            for s in all_sources[:MAX_UNIFIED_SUGGESTIONS]
        ]

    # Score and merge results
    scored: List[tuple[SuggestionSource, float]] = []

    for fs in file_sources:
        scored.append((fs, fs.score if fs.score is not None else 0.5))

    # Simple scoring for non-file sources
    query_lower = query.lower()
    for source in [*mcp_sources, *agent_sources]:
        text = source.display_text.lower()
        if query_lower in text:
            pos = text.index(query_lower)
            score = pos / max(len(text), 1)
            scored.append((source, score))
        elif any(c in text for c in query_lower):
            scored.append((source, 0.6))

    scored.sort(key=lambda x: x[1])

    return [
        create_suggestion_from_source(s)
        for s, _ in scored[:MAX_UNIFIED_SUGGESTIONS]
    ]
