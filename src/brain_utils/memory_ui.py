"""Memory display utilities for JARVIS.

Provides formatting functions for memory stats, entries, search,
and ASCII graph visualization of memory connections.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


def format_memory_stats(memory_store: Any) -> str:
    """Format memory usage statistics for CLI display.

    Expects *memory_store* to expose attributes or methods commonly
    found on the JARVIS MemoryStore:
      - turn_count or get_turn_count()
      - node_count or get_node_count()  (neural lattice nodes)
      - synapse_count or get_synapse_count()
      - db_path

    Gracefully handles missing attributes.

    Args:
        memory_store: A MemoryStore-like object.

    Returns:
        Formatted multi-line stats string.
    """
    lines: list[str] = ["Memory Statistics", ""]

    def _get(obj: Any, name: str) -> Any:
        """Try attr, then callable attr, then return None."""
        val = getattr(obj, name, None)
        if val is not None:
            return val() if callable(val) else val
        getter = getattr(obj, f"get_{name}", None)
        if getter and callable(getter):
            return getter()
        return None

    turn_count = _get(memory_store, "turn_count")
    node_count = _get(memory_store, "node_count")
    synapse_count = _get(memory_store, "synapse_count")
    db_path = _get(memory_store, "db_path")

    if turn_count is not None:
        lines.append(f"  Conversation turns: {turn_count}")
    if node_count is not None:
        lines.append(f"  Knowledge nodes:    {node_count}")
    if synapse_count is not None:
        lines.append(f"  Synapses:           {synapse_count}")
    if db_path is not None:
        lines.append(f"  Database: {db_path}")

    if len(lines) == 2:
        lines.append("  No stats available.")

    return "\n".join(lines)


def format_memory_entry(entry: dict) -> str:
    """Format a single memory entry for display.

    Expected keys: key, value, timestamp (optional), source (optional).

    Args:
        entry: Dict with memory entry data.

    Returns:
        Formatted string for the entry.
    """
    key = entry.get("key", "unknown")
    value = entry.get("value", "")
    timestamp = entry.get("timestamp", "")
    source = entry.get("source", "")

    # Format timestamp if it looks like ISO
    ts_display = ""
    if timestamp:
        try:
            dt = datetime.fromisoformat(str(timestamp))
            ts_display = dt.strftime("%Y-%m-%d %H:%M")
        except (ValueError, TypeError):
            ts_display = str(timestamp)

    parts: list[str] = [f"  {key}: {value}"]
    meta: list[str] = []
    if ts_display:
        meta.append(ts_display)
    if source:
        meta.append(f"source: {source}")
    if meta:
        parts.append(f"    ({', '.join(meta)})")

    return "\n".join(parts)


def search_memory_entries(entries: list[dict], query: str) -> list[dict]:
    """Filter memory entries by a search query.

    Matches if any query term (case-insensitive) appears in the entry's
    key or value.

    Args:
        entries: List of memory entry dicts (expected keys: key, value).
        query: Space-separated search terms.

    Returns:
        Filtered list of matching entries.
    """
    if not query.strip():
        return list(entries)

    terms = query.lower().split()
    results: list[dict] = []

    for entry in entries:
        key = str(entry.get("key", "")).lower()
        value = str(entry.get("value", "")).lower()
        haystack = f"{key} {value}"
        if any(term in haystack for term in terms):
            results.append(entry)

    return results


def format_memory_graph(
    nodes: list, edges: list, max_width: int = 80
) -> str:
    """Render a simple ASCII graph of memory connections.

    Each node is expected to have an 'id' and 'label' (or be a string).
    Each edge is expected to have 'source' and 'target' (node IDs or
    indices), with an optional 'weight'.

    Args:
        nodes: List of node dicts or strings.
        edges: List of edge dicts with source/target keys.
        max_width: Maximum line width for the output.

    Returns:
        ASCII art string representing the graph.
    """
    if not nodes:
        return "  (empty graph)"

    # Normalize nodes to {id, label}
    node_map: dict[str, str] = {}
    for i, n in enumerate(nodes):
        if isinstance(n, dict):
            nid = str(n.get("id", i))
            label = str(n.get("label", n.get("id", f"node_{i}")))
        else:
            nid = str(i)
            label = str(n)
        node_map[nid] = label

    # Build adjacency
    adj: dict[str, list[tuple[str, float]]] = {nid: [] for nid in node_map}
    for e in edges:
        if isinstance(e, dict):
            src = str(e.get("source", ""))
            tgt = str(e.get("target", ""))
            weight = float(e.get("weight", 1.0))
        else:
            continue
        if src in adj and tgt in node_map:
            adj[src].append((tgt, weight))

    # Render
    lines: list[str] = ["Memory Graph", ""]

    for nid, label in node_map.items():
        # Truncate label to fit
        max_label = max_width - 6
        display = label[:max_label] if len(label) > max_label else label
        connections = adj.get(nid, [])

        lines.append(f"  [{display}]")
        for tgt, weight in connections:
            tgt_label = node_map.get(tgt, tgt)
            tgt_display = (
                tgt_label[: max_label - 10]
                if len(tgt_label) > max_label - 10
                else tgt_label
            )
            weight_str = f" ({weight:.1f})" if weight != 1.0 else ""
            lines.append(f"    --> [{tgt_display}]{weight_str}")

    return "\n".join(lines)
