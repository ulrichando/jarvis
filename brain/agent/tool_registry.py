"""Formal tool registration system for JARVIS agent tools.

Provides structured metadata for each tool beyond the raw JSON schema,
enabling smarter concurrency decisions, output management, and deferred
tool loading. Works alongside tools.py -- does not replace it.

Inspired by Claude Code's tool architecture.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Callable


# ---------------------------------------------------------------------------
# Core data types
# ---------------------------------------------------------------------------

@dataclass
class ToolMeta:
    """Rich metadata for a single tool."""

    name: str
    description: str
    parameters: dict
    is_read_only: bool = False
    is_concurrency_safe: bool = False
    is_destructive: bool = False
    max_result_size: int = 16_000
    should_defer: bool = False
    search_hint: str = ""
    category: str = "general"
    activity_description: Callable[..., str] | None = None


@dataclass
class ToolResult:
    """Outcome of a tool execution, with optional truncation info."""

    content: str
    is_truncated: bool = False
    persisted_path: str | None = None
    original_size: int = 0


# ---------------------------------------------------------------------------
# Registry: one global dict mapping name -> ToolMeta
# ---------------------------------------------------------------------------

TOOL_REGISTRY: dict[str, ToolMeta] = {}


def _build_registry() -> None:
    """Populate TOOL_REGISTRY from the TOOL_SCHEMAS defined in tools.py."""

    from brain.agent.tools import TOOL_SCHEMAS

    # Per-tool overrides keyed by function name.
    overrides: dict[str, dict] = {
        "bash": dict(
            category="system",
            activity_description=lambda args: f"Running: {args.get('command', '???')[:60]}",
        ),
        "read_file": dict(
            is_read_only=True,
            is_concurrency_safe=True,
            category="file",
            activity_description=lambda args: f"Reading {os.path.basename(args.get('path', '?'))}",
        ),
        "write_file": dict(
            category="file",
            activity_description=lambda args: f"Writing {os.path.basename(args.get('path', '?'))}",
        ),
        "edit_file": dict(
            category="file",
            activity_description=lambda args: f"Editing {os.path.basename(args.get('path', '?'))}",
        ),
        "search_files": dict(
            is_read_only=True,
            is_concurrency_safe=True,
            category="search",
            activity_description=lambda args: f"Searching: {args.get('pattern', '?')}",
        ),
        "web_search": dict(
            is_read_only=True,
            is_concurrency_safe=True,
            category="web",
            activity_description=lambda args: f"Searching web: {args.get('query', '?')[:50]}",
        ),
        "web_fetch": dict(
            is_read_only=True,
            is_concurrency_safe=True,
            category="web",
            activity_description=lambda args: f"Fetching {args.get('url', '?')[:60]}",
        ),
        "web_api": dict(
            category="web",
            activity_description=lambda args: (
                f"{args.get('method', 'GET')} {args.get('url', '?')[:50]}"
            ),
        ),
        "database": dict(
            category="system",
            activity_description=lambda args: f"Query on {os.path.basename(args.get('database', '?'))}",
        ),
        "computer_use": dict(
            category="system",
            activity_description=lambda args: f"Computer: {args.get('action', '?')}",
        ),
        "view_screen": dict(
            is_read_only=True,
            is_concurrency_safe=True,
            category="system",
            activity_description=lambda _: "Viewing screen",
        ),
        "think": dict(
            is_read_only=True,
            is_concurrency_safe=True,
            max_result_size=0,  # no cap
            category="agent",
            activity_description=lambda _: "Thinking...",
        ),
        "tool_search": dict(
            is_read_only=True,
            is_concurrency_safe=True,
            category="agent",
            activity_description=lambda args: f"Searching tools: {args.get('query', '?')[:40]}",
        ),
        "dispatch": dict(
            category="agent",
            activity_description=lambda args: (
                f"Dispatching {args.get('agent_type', '?')}: "
                f"{args.get('task', '?')[:40]}"
            ),
        ),
    }

    for schema in TOOL_SCHEMAS:
        func = schema.get("function", schema)
        name = func["name"]
        desc = func.get("description", "")
        params = func.get("parameters", {})

        kw = overrides.get(name, {})
        meta = ToolMeta(
            name=name,
            description=desc,
            parameters=params,
            **{k: v for k, v in kw.items()},
        )
        TOOL_REGISTRY[name] = meta


# Build on import so the registry is ready immediately.
_build_registry()


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def get_concurrency_safe_tools() -> set[str]:
    """Return the names of tools safe for parallel execution."""
    return {name for name, meta in TOOL_REGISTRY.items() if meta.is_concurrency_safe}


def get_read_only_tools() -> set[str]:
    """Return the names of tools that only read state."""
    return {name for name, meta in TOOL_REGISTRY.items() if meta.is_read_only}


def get_deferred_tools() -> list[ToolMeta]:
    """Return tools marked as deferred (not sent to LLM by default)."""
    return [meta for meta in TOOL_REGISTRY.values() if meta.should_defer]


def get_active_tools(include_deferred: bool = False) -> list[dict]:
    """Return tool schemas in the standard function-calling format.

    By default, deferred tools are excluded so they don't consume prompt
    space unless explicitly requested.
    """
    from brain.agent.tools import TOOL_SCHEMAS

    if include_deferred:
        return list(TOOL_SCHEMAS)

    deferred_names = {m.name for m in get_deferred_tools()}
    return [
        s for s in TOOL_SCHEMAS
        if s.get("function", s)["name"] not in deferred_names
    ]


def search_tools(query: str, max_results: int = 5) -> list[ToolMeta]:
    """Keyword search across deferred tools by name, description, and search_hint.

    Returns up to *max_results* matches ordered by relevance (simple
    keyword-overlap scoring).
    """
    query_lower = query.lower()
    tokens = query_lower.split()

    scored: list[tuple[float, ToolMeta]] = []
    for meta in TOOL_REGISTRY.values():
        if not meta.should_defer:
            continue
        haystack = f"{meta.name} {meta.description} {meta.search_hint}".lower()
        # Count how many query tokens appear in the haystack.
        hits = sum(1 for t in tokens if t in haystack)
        if hits > 0:
            scored.append((hits, meta))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [meta for _, meta in scored[:max_results]]


def get_tool_meta(name: str) -> ToolMeta | None:
    """Look up a tool's metadata by name."""
    return TOOL_REGISTRY.get(name)


def register_tool(meta: ToolMeta) -> None:
    """Register a new tool (e.g. from MCP or a plugin)."""
    TOOL_REGISTRY[meta.name] = meta


def get_result_size_limit(name: str) -> int:
    """Return the max result size (in chars) for a given tool.

    Falls back to 16 000 if the tool is not in the registry.
    A limit of 0 means unlimited.
    """
    meta = TOOL_REGISTRY.get(name)
    if meta is None:
        return 16_000
    return meta.max_result_size


# ---------------------------------------------------------------------------
# Large-result persistence
# ---------------------------------------------------------------------------

def persist_large_result(
    tool_name: str,
    tool_use_id: str,
    content: str,
    session_dir: str,
) -> ToolResult:
    """Persist a tool result if it exceeds the tool's max_result_size.

    When the content is over the limit the full text is written to
    ``{session_dir}/tool-results/{tool_use_id}.json`` and a truncated
    preview is returned so the LLM still gets useful context without
    blowing up the conversation window.

    Returns a ``ToolResult`` in all cases.
    """
    original_size = len(content)
    limit = get_result_size_limit(tool_name)

    # 0 means unlimited -- never truncate.
    if limit == 0 or original_size <= limit:
        return ToolResult(
            content=content,
            is_truncated=False,
            original_size=original_size,
        )

    # Persist the full output to disk.
    results_dir = os.path.join(session_dir, "tool-results")
    os.makedirs(results_dir, exist_ok=True)
    out_path = os.path.join(results_dir, f"{tool_use_id}.json")

    payload = {
        "tool": tool_name,
        "tool_use_id": tool_use_id,
        "size": original_size,
        "content": content,
    }
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)

    # Build a human-friendly preview.
    preview_size = min(2000, limit)
    preview = content[:preview_size]
    truncation_note = (
        f"\n\n[... truncated: {original_size} chars total, "
        f"full output saved to {out_path} ...]"
    )

    return ToolResult(
        content=preview + truncation_note,
        is_truncated=True,
        persisted_path=out_path,
        original_size=original_size,
    )
