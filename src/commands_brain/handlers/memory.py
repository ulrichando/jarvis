"""Memory commands -- lattice inspection, learn, recall, knowledge browsing, auto-memory."""
import os
from pathlib import Path

from src.commands_brain.registry import command, CommandContext, CommandResult, PermLevel


@command("memory", aliases=["mem"], description="Memory management: stats, show, search, edit",
         usage="/memory [show|search <query>|edit <name>|stats]", category="memory", permission=PermLevel.READ_ONLY)
async def cmd_memory(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)

    args = ctx.args.strip()
    parts = args.split(None, 1)
    action = parts[0].lower() if parts else "stats"
    rest = parts[1].strip() if len(parts) > 1 else ""

    # ── /memory show ──
    if action == "show":
        try:
            from src.memory.auto_memory import get_memory_extractor
            extractor = get_memory_extractor()
            memories = extractor.load_existing_memories()
            if not memories:
                return CommandResult(text="No memory files found.")
            lines = ["Memory Files", "=" * 55]
            for m in memories:
                name = m.get("name", "?")
                desc = m.get("description", "")[:50]
                mtype = m.get("type", "?")
                mpath = m.get("path", "")
                size = 0
                if mpath and os.path.exists(mpath):
                    size = os.path.getsize(mpath)
                size_str = f"{size}B" if size < 1024 else f"{size / 1024:.1f}KB"
                lines.append(f"  [{mtype:<9s}] {name:<25s} {size_str:>8s}  {desc}")
            lines.append(f"\n  {len(memories)} memory file(s)")
            return CommandResult(text="\n".join(lines))
        except ImportError:
            return CommandResult(text="Auto-memory module not available.", success=False)

    # ── /memory search <query> ──
    if action == "search":
        if not rest:
            return CommandResult(text="Usage: /memory search <query>", success=False)
        try:
            from src.memory.auto_memory import get_memory_extractor
            extractor = get_memory_extractor()
            memories = extractor.load_existing_memories()
            query_lower = rest.lower()
            matches = [
                m for m in memories
                if query_lower in m.get("content", "").lower()
                or query_lower in m.get("name", "").lower()
                or query_lower in m.get("description", "").lower()
            ]
            if not matches:
                return CommandResult(text=f"No memories matching '{rest}'.")
            lines = [f"Memory Search: '{rest}' ({len(matches)} results)", "-" * 40]
            for m in matches:
                name = m.get("name", "?")
                content = m.get("content", "")[:80].replace("\n", " ")
                lines.append(f"  {name}: {content}")
            return CommandResult(text="\n".join(lines))
        except ImportError:
            return CommandResult(text="Auto-memory module not available.", success=False)

    # ── /memory edit <name> ──
    if action == "edit":
        if not rest:
            return CommandResult(text="Usage: /memory edit <name>", success=False)
        try:
            from src.config import JARVIS_HOME
            memory_dir = JARVIS_HOME / "memory"
            # Find matching file
            matches = list(memory_dir.glob(f"*{rest}*"))
            if not matches:
                return CommandResult(text=f"No memory file matching '{rest}'.", success=False)
            target = matches[0]
            editor = os.environ.get("EDITOR", "nano")
            return CommandResult(
                text=f"Open in editor: {editor} {target}\n"
                     f"(Run this command in your terminal to edit)",
                data={"edit_command": f"{editor} {target}", "path": str(target)},
            )
        except ImportError:
            return CommandResult(text="Config module not available.", success=False)

    # ── /memory stats (default) ──
    mem = brain.memory
    stats = mem.stats if hasattr(mem, 'stats') else {}
    nodes = stats.get("lattice_nodes", 0)
    synapses = stats.get("synapses", 0)
    domains = stats.get("domains", [])

    lines = ["Memory Stats", "=" * 40]
    lines.append(f"  Nodes:    {nodes}")
    lines.append(f"  Synapses: {synapses}")
    lines.append(f"  Domains:  {', '.join(domains) if domains else 'none'}")

    # Memory file stats
    try:
        from src.config import JARVIS_HOME
        memory_dir = JARVIS_HOME / "memory"
        if memory_dir.is_dir():
            md_files = list(memory_dir.glob("*.md"))
            total_size = sum(f.stat().st_size for f in md_files if f.is_file())
            size_str = f"{total_size / 1024:.1f}KB" if total_size > 1024 else f"{total_size}B"
            lines.append(f"\n  Memory Files: {len(md_files)} ({size_str} total)")
    except ImportError:
        pass

    # Strength distribution
    if hasattr(mem, 'lattice') and hasattr(mem.lattice, 'strength_distribution'):
        dist = mem.lattice.strength_distribution()
        lines.append(f"\n  Strength Distribution")
        lines.append("  " + "-" * 22)
        for bucket, count in dist.items():
            bar = "#" * min(count, 30)
            lines.append(f"  {bucket:<12s} {count:>5d} {bar}")

    return CommandResult(text="\n".join(lines))


@command("learn", description="Add knowledge to memory lattice",
         usage="/learn <text> [--type fact|skill|entity] [--tags tag1,tag2]",
         category="memory", permission=PermLevel.STANDARD)
async def cmd_learn(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)

    args = ctx.args.strip()
    if not args:
        return CommandResult(text="Usage: /learn <text> [--type fact|skill|entity] [--tags tag1,tag2]", success=False)

    # Parse flags
    node_type = "fact"
    tags = []
    text_parts = []

    tokens = args.split()
    i = 0
    while i < len(tokens):
        if tokens[i] == "--type" and i + 1 < len(tokens):
            node_type = tokens[i + 1]
            i += 2
        elif tokens[i] == "--tags" and i + 1 < len(tokens):
            tags = [t.strip() for t in tokens[i + 1].split(",") if t.strip()]
            i += 2
        else:
            text_parts.append(tokens[i])
            i += 1

    text = " ".join(text_parts)
    if not text:
        return CommandResult(text="No text provided to learn.", success=False)

    node_id = brain.memory.lattice.add_node(
        content=text,
        node_type=node_type,
        tags=tags,
    )
    return CommandResult(text=f"Learned ({node_type}): {text[:80]}\nNode: {node_id[:8]}")


@command("recall", description="Search memories by query, showing source and relevance",
         usage="/recall <query> [--top_k N]", category="memory", permission=PermLevel.READ_ONLY)
async def cmd_recall(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)

    args = ctx.args.strip()
    if not args:
        return CommandResult(text="Usage: /recall <query> [--top_k N]", success=False)

    # Parse --top_k
    top_k = 5
    tokens = args.split()
    query_parts = []
    i = 0
    while i < len(tokens):
        if tokens[i] == "--top_k" and i + 1 < len(tokens):
            top_k = int(tokens[i + 1])
            i += 2
        else:
            query_parts.append(tokens[i])
            i += 1

    query = " ".join(query_parts)
    results = brain.memory.lattice.search(query, top_k=top_k)

    # Also search auto-memory files for additional context
    auto_results = []
    try:
        from src.memory.auto_memory import get_memory_extractor
        extractor = get_memory_extractor()
        memories = extractor.load_existing_memories()
        query_lower = query.lower()
        for m in memories:
            content = m.get("content", "").lower()
            name = m.get("name", "").lower()
            if query_lower in content or query_lower in name:
                auto_results.append(m)
    except ImportError:
        pass

    if not results and not auto_results:
        return CommandResult(text=f"No memories found for: {query}")

    lines = [f"Recall: \"{query}\"", "-" * 50]

    if results:
        lines.append(f"\n  Lattice Results ({len(results)}):")
        for r in results:
            nid = r.get("id", "?")[:8]
            score = r.get("score", 0.0)
            content = r.get("content", "")[:100].replace("\n", " ")
            ntype = r.get("type", "?")
            source = r.get("domain", r.get("source", "lattice"))
            # Relevance indicator
            if score > 0.8:
                rel = "HIGH"
            elif score > 0.5:
                rel = "MED"
            else:
                rel = "LOW"
            lines.append(f"  [{nid}] [{rel:>4s} {score:.2f}] [{ntype}] {content}")
            if source and source != "lattice":
                lines.append(f"         source: {source}")

    if auto_results:
        lines.append(f"\n  Memory Files ({len(auto_results)}):")
        for m in auto_results[:top_k]:
            name = m.get("name", "?")
            content = m.get("content", "")[:80].replace("\n", " ")
            mpath = m.get("path", "")
            filename = Path(mpath).name if mpath else "?"
            lines.append(f"  [{filename}] {name}: {content}")

    return CommandResult(text="\n".join(lines))


@command("forget", description="Remove a memory by ID, query, or memory file name",
         usage="/forget <node_id_or_query_or_filename>", category="memory", permission=PermLevel.FULL)
async def cmd_forget(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)

    target = ctx.args.strip()
    if not target:
        return CommandResult(text="Usage: /forget <node_id_or_query_or_filename>", success=False)

    # Try as memory file name first
    try:
        from src.config import JARVIS_HOME
        memory_dir = JARVIS_HOME / "memory"
        if memory_dir.is_dir():
            matches = list(memory_dir.glob(f"*{target}*"))
            matches = [m for m in matches if m.name != "MEMORY.md"]
            if matches:
                removed_file = matches[0]
                content_preview = removed_file.read_text(errors="replace")[:100].replace("\n", " ")
                removed_file.unlink()
                # Update index
                try:
                    from src.memory.auto_memory import get_memory_extractor
                    get_memory_extractor().update_index()
                except Exception:
                    pass
                return CommandResult(
                    text=f"Removed memory file: {removed_file.name}\n"
                         f"  Content: {content_preview}"
                )
    except ImportError:
        pass

    lattice = brain.memory.lattice

    # Try as node ID
    if lattice.has_node(target):
        lattice.remove_node(target)
        return CommandResult(text=f"Removed node: {target[:8]}")

    # Try as query -- find best match and show what was removed
    results = lattice.search(target, top_k=1)
    if results:
        node = results[0]
        nid = node.get("id", "?")
        content = node.get("content", "")[:80]
        ntype = node.get("type", "?")
        score = node.get("score", 0.0)
        lattice.remove_node(nid)
        return CommandResult(
            text=f"Removed closest match:\n"
                 f"  ID:      {nid[:8]}\n"
                 f"  Type:    {ntype}\n"
                 f"  Score:   {score:.2f}\n"
                 f"  Content: {content}"
        )

    return CommandResult(text=f"No memory found matching: {target}", success=False)


@command("knowledge", aliases=["kb"], description="Browse memories by domain",
         usage="/knowledge [domain]", category="memory", permission=PermLevel.READ_ONLY)
async def cmd_knowledge(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)

    domain = ctx.args.strip().lower() or None
    lattice = brain.memory.lattice

    if not domain:
        # List available domains
        domains = lattice.list_domains()
        if not domains:
            return CommandResult(text="No knowledge domains found.")
        lines = ["Knowledge Domains", "=" * 40]
        for d in domains:
            count = d.get("count", 0)
            lines.append(f"  {d['name']:<20s} {count:>5d} nodes")
        return CommandResult(text="\n".join(lines))

    # List nodes in domain
    nodes = lattice.get_by_domain(domain, limit=20)
    if not nodes:
        return CommandResult(text=f"No knowledge found in domain: {domain}")

    lines = [f"Knowledge: {domain} ({len(nodes)} shown)", "-" * 40]
    for n in nodes:
        nid = n.get("id", "?")[:8]
        content = n.get("content", "")[:80].replace("\n", " ")
        lines.append(f"  [{nid}] {content}")
    return CommandResult(text="\n".join(lines))


@command("lattice", description="Show lattice structure stats",
         usage="/lattice [--verbose]", category="memory", permission=PermLevel.READ_ONLY)
async def cmd_lattice(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)

    verbose = "--verbose" in ctx.args or "-v" in ctx.args
    lattice = brain.memory.lattice

    info = lattice.info()
    lines = ["NeuralLattice Structure", "=" * 40]
    lines.append(f"  Nodes:       {info.get('nodes', 0)}")
    lines.append(f"  Synapses:    {info.get('synapses', 0)}")
    lines.append(f"  Clusters:    {info.get('clusters', 0)}")
    lines.append(f"  Avg Degree:  {info.get('avg_degree', 0.0):.1f}")
    lines.append(f"  Domains:     {info.get('domain_count', 0)}")

    if verbose:
        lines.append(f"\n  Node Types")
        lines.append("  " + "-" * 15)
        for ntype, count in info.get("type_counts", {}).items():
            lines.append(f"  {ntype:<15s} {count:>5d}")

        lines.append(f"\n  Top Hubs (most connected)")
        lines.append("  " + "-" * 25)
        for hub in info.get("top_hubs", [])[:10]:
            lines.append(f"  [{hub['id'][:8]}] degree={hub['degree']:<4d} {hub.get('label', '')[:40]}")

    return CommandResult(text="\n".join(lines))


@command("consolidate", description="Trigger memory maintenance (decay, prune, compress)",
         usage="/consolidate", category="memory", permission=PermLevel.STANDARD)
async def cmd_consolidate(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)

    lattice = brain.memory.lattice
    result = lattice.consolidate()

    lines = ["Memory Consolidation Complete", "-" * 35]
    lines.append(f"  Decayed:    {result.get('decayed', 0)} synapses weakened")
    lines.append(f"  Pruned:     {result.get('pruned', 0)} dead nodes removed")
    lines.append(f"  Compressed: {result.get('compressed', 0)} nodes merged")
    lines.append(f"  Duration:   {result.get('duration_ms', 0):.0f}ms")
    return CommandResult(text="\n".join(lines))


@command("associations", aliases=["assoc"], description="Show connected memories for a concept",
         usage="/associations <concept>", category="memory", permission=PermLevel.READ_ONLY)
async def cmd_associations(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)

    concept = ctx.args.strip()
    if not concept:
        return CommandResult(text="Usage: /associations <concept>", success=False)

    lattice = brain.memory.lattice

    # Find the concept node
    results = lattice.search(concept, top_k=1)
    if not results:
        return CommandResult(text=f"No memory found for: {concept}", success=False)

    root = results[0]
    root_id = root.get("id")
    neighbors = lattice.get_neighbors(root_id, max_depth=2)

    lines = [f"Associations for: {concept}", f"  Root: [{root_id[:8]}] {root.get('content', '')[:60]}", "-" * 40]

    if not neighbors:
        lines.append("  No associations found.")
    else:
        for n in neighbors:
            nid = n.get("id", "?")[:8]
            strength = n.get("strength", 0.0)
            depth = n.get("depth", 1)
            content = n.get("content", "")[:60].replace("\n", " ")
            indent = "  " * depth
            lines.append(f"  {indent}[{nid}] (str={strength:.2f}, d={depth}) {content}")

    return CommandResult(text="\n".join(lines))


@command("common-sense", aliases=["cs"], description="Query common sense knowledge base",
         usage="/common-sense <query>", category="memory", permission=PermLevel.READ_ONLY)
async def cmd_common_sense(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)

    query = ctx.args.strip()
    if not query:
        return CommandResult(text="Usage: /common-sense <query>", success=False)

    # Check for common sense KB
    if not hasattr(brain.memory, 'common_sense'):
        return CommandResult(text="Common sense KB not loaded.", success=False)

    results = brain.memory.common_sense.query(query, top_k=10)
    if not results:
        return CommandResult(text=f"No common sense knowledge for: {query}")

    lines = [f"Common Sense: \"{query}\"", "-" * 40]
    for r in results:
        relation = r.get("relation", "?")
        subject = r.get("subject", "?")
        obj = r.get("object", "?")
        weight = r.get("weight", 0.0)
        lines.append(f"  {subject} --[{relation}]--> {obj}  (w={weight:.2f})")
    return CommandResult(text="\n".join(lines))


@command("user-profile", aliases=["profile"], description="View or edit user preferences",
         usage="/user-profile [key] [value]", category="memory", permission=PermLevel.STANDARD)
async def cmd_user_profile(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)

    args = ctx.args.strip().split(maxsplit=1)
    key = args[0] if args else None
    value = args[1] if len(args) > 1 else None

    profile = brain.memory.user_profile

    if not key:
        # Show all
        data = profile.to_dict()
        if not data:
            return CommandResult(text="User profile is empty.")
        lines = ["User Profile", "=" * 40]
        for k, v in sorted(data.items()):
            lines.append(f"  {k:<20s} {v}")
        return CommandResult(text="\n".join(lines))

    if value is None:
        # Get specific key
        val = profile.get(key)
        if val is None:
            return CommandResult(text=f"Key not found: {key}", success=False)
        return CommandResult(text=f"{key}: {val}")

    # Set key
    profile.set(key, value)
    return CommandResult(text=f"Set {key} = {value}")


# ── /dream ────────────────────────────────────────────────────────────

@command("dream", description="Consolidate session learnings into memory files",
         usage="/dream", category="memory", permission=PermLevel.STANDARD)
async def cmd_dream(ctx: CommandContext) -> CommandResult:
    """Extract key decisions, patterns, and preferences from recent conversation
    and save to memory files via auto_memory."""
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)

    try:
        from src.memory.auto_memory import get_memory_extractor
    except ImportError:
        return CommandResult(text="Auto-memory module not available.", success=False)

    # Gather recent conversation history
    messages = []
    if hasattr(brain, 'memory') and hasattr(brain.memory, 'get_history'):
        try:
            messages = brain.memory.get_history(limit=50)
        except Exception:
            pass

    if not messages and hasattr(brain, 'conversation_history'):
        messages = brain.conversation_history[-50:] if brain.conversation_history else []

    if not messages:
        return CommandResult(text="No conversation history available to dream on.", success=False)

    extractor = get_memory_extractor()

    # Extract and save
    saved = extractor.extract_and_save(messages)

    # Also load what we have now for summary
    all_memories = extractor.load_existing_memories()

    lines = ["Dream Complete -- Session Consolidation", "=" * 45]
    lines.append(f"  Messages analyzed: {len(messages)}")
    lines.append(f"  New memories saved: {saved}")
    lines.append(f"  Total memory files: {len(all_memories)}")

    if saved > 0:
        lines.append(f"\n  New Memories:")
        # Show the most recent ones (they'll be at the end)
        recent = all_memories[-saved:] if saved <= len(all_memories) else all_memories
        for m in recent:
            name = m.get("name", "?")
            mtype = m.get("type", "?")
            desc = m.get("description", "")[:60]
            lines.append(f"    [{mtype}] {name}: {desc}")
    else:
        lines.append("\n  No new learnings extracted (existing memories cover this session).")

    return CommandResult(text="\n".join(lines))
