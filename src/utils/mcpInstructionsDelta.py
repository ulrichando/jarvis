"""MCP instructions delta computation for tracking server instruction changes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class McpInstructionsDelta:
    """Delta of MCP server instruction changes."""
    added_names: list[str] = field(default_factory=list)
    added_blocks: list[str] = field(default_factory=list)
    removed_names: list[str] = field(default_factory=list)


@dataclass
class ClientSideInstruction:
    """Client-authored instruction block for an MCP server."""
    server_name: str
    block: str


def get_mcp_instructions_delta(
    mcp_clients: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    client_side_instructions: list[ClientSideInstruction],
) -> Optional[McpInstructionsDelta]:
    """Diff current connected MCP servers against what's been announced.

    Returns None if nothing changed.

    Args:
        mcp_clients: List of MCP server connection dicts with 'type', 'name', 'instructions' keys
        messages: Conversation message history
        client_side_instructions: Client-side instruction blocks
    """
    # Scan announced servers from message history
    announced: set[str] = set()
    for msg in messages:
        if msg.get("type") != "attachment":
            continue
        attachment = msg.get("attachment", {})
        if attachment.get("type") != "mcp_instructions_delta":
            continue
        for n in attachment.get("addedNames", []):
            announced.add(n)
        for n in attachment.get("removedNames", []):
            announced.discard(n)

    # Filter to connected servers
    connected = [c for c in mcp_clients if c.get("type") == "connected"]
    connected_names = {c["name"] for c in connected}

    # Build instruction blocks
    blocks: dict[str, str] = {}
    for c in connected:
        instructions = c.get("instructions")
        if instructions:
            blocks[c["name"]] = f"## {c['name']}\n{instructions}"

    for ci in client_side_instructions:
        if ci.server_name not in connected_names:
            continue
        existing = blocks.get(ci.server_name)
        if existing:
            blocks[ci.server_name] = f"{existing}\n\n{ci.block}"
        else:
            blocks[ci.server_name] = f"## {ci.server_name}\n{ci.block}"

    # Find newly added servers
    added: list[tuple[str, str]] = []
    for name, block in blocks.items():
        if name not in announced:
            added.append((name, block))

    # Find removed servers
    removed = sorted(n for n in announced if n not in connected_names)

    if not added and not removed:
        return None

    added.sort(key=lambda x: x[0])
    return McpInstructionsDelta(
        added_names=[a[0] for a in added],
        added_blocks=[a[1] for a in added],
        removed_names=removed,
    )
