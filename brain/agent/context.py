"""JARVIS Context Manager — prevents context overflow in the agent loop.

Inspired by Claude Code's compaction strategy:
- Adaptive compaction based on model context limits
- Three-phase approach: truncate tool results → summarize → drop
- Preserves critical recent context and system prompt
- Tracks token usage for status display
"""

import re

# Rough token estimation (4 chars ≈ 1 token for English)
CHARS_PER_TOKEN = 4

# Model limits (conservative to leave room for response + tools)
MODEL_LIMITS = {
    # Claude models
    "claude-opus-4-6-20250514": 900000,    # 1M context
    "claude-sonnet-4-6-20250514": 900000,  # 1M context
    "claude-sonnet-4-20250514": 180000,    # 200K context
    "claude-haiku-4-5-20251001": 180000,   # 200K context
    # Local models
    "llama3.3:70b": 120000,
    "qwen2.5:72b": 120000,
    "qwen2.5:7b": 28000,
    "deepseek-coder-v2:16b": 28000,
    # Cloud models
    "deepseek-chat": 60000,
    "gpt-4o": 120000,
    "gpt-4o-mini": 120000,
}

DEFAULT_MAX_TOKENS = 180000  # Safe default for Claude


def estimate_tokens(messages: list[dict]) -> int:
    """Rough token count for a message list."""
    total = 0
    for msg in messages:
        content = msg.get("content", "") or ""
        total += len(content) // CHARS_PER_TOKEN
        # Tool calls add tokens too
        if "tool_calls" in msg:
            for tc in msg["tool_calls"]:
                args = tc.get("function", {}).get("arguments", "")
                total += len(args) // CHARS_PER_TOKEN + 20  # overhead
    return total


def compact_messages(
    messages: list[dict],
    max_tokens: int = DEFAULT_MAX_TOKENS,
    preserve_recent: int = 6,
) -> list[dict]:
    """Compact messages to fit within token budget.

    Three-phase strategy (inspired by Claude Code):
    1. Truncate old tool results (biggest token consumers)
    2. Summarize old conversation turns
    3. Drop oldest messages entirely if still over budget
    """
    current_tokens = estimate_tokens(messages)

    # Under budget — no compaction needed
    if current_tokens <= max_tokens:
        return messages

    # Split: system + old messages + recent messages
    system = [messages[0]] if messages and messages[0]["role"] == "system" else []
    recent_start = max(len(system), len(messages) - preserve_recent)
    old = messages[len(system):recent_start]
    recent = messages[recent_start:]

    # Phase 1: Truncate old tool results (they're the biggest consumers)
    compacted_old = []
    for msg in old:
        if msg["role"] == "tool":
            content = msg.get("content", "")
            if len(content) > 500:
                lines = content.split("\n")
                if len(lines) > 5:
                    truncated = "\n".join(lines[:3]) + f"\n... ({len(lines)-4} lines omitted)\n" + lines[-1]
                else:
                    truncated = content[:300] + "..."
                compacted_old.append({**msg, "content": truncated})
            else:
                compacted_old.append(msg)
        elif msg["role"] == "assistant" and "tool_calls" in msg:
            compacted_old.append(msg)
        else:
            content = msg.get("content", "") or ""
            if len(content) > 300:
                compacted_old.append({**msg, "content": content[:300] + "..."})
            else:
                compacted_old.append(msg)

    result = system + compacted_old + recent

    # Phase 2: If still over, inject a summary and drop old messages
    if estimate_tokens(result) > max_tokens and len(compacted_old) > 2:
        summary = build_context_summary(compacted_old)
        summary_msg = {
            "role": "user",
            "content": f"[Previous conversation summary]\n{summary}",
        }
        result = system + [summary_msg] + recent

    # Phase 3: Drop oldest messages one by one until under budget
    while estimate_tokens(result) > max_tokens and len(result) > len(system) + preserve_recent + 1:
        result.pop(len(system))

    return result


def build_context_summary(messages: list[dict]) -> str:
    """Summarize a list of messages into a compact context string.

    Extracts richer information for better context preservation:
    - Conversation flow (user requests + assistant responses)
    - File references (paths with extensions)
    - Pending work items (todo, next, pending, follow up)
    - Tool usage summary with call counts
    - Recent user requests (last 3)
    """
    # Pattern for file paths with extensions
    _FILE_RE = re.compile(r'(?:^|[\s\'"`(])(/[\w./-]+\.\w{1,10})\b')
    # Keywords that signal pending work
    _PENDING_KW = re.compile(r'\b(todo|next|pending|follow[- ]?up|fixme|hack|remaining)\b', re.IGNORECASE)

    parts = []
    file_refs: set[str] = set()
    pending_items: list[str] = []
    tool_counts: dict[str, int] = {}
    user_requests: list[str] = []

    for msg in messages:
        role = msg.get("role", "?")
        content = (msg.get("content", "") or "")

        # --- Extract file references from all messages ---
        for match in _FILE_RE.finditer(content):
            file_refs.add(match.group(1))

        # --- Detect pending work ---
        if _PENDING_KW.search(content):
            # Grab the line containing the keyword for context
            for line in content.split("\n"):
                if _PENDING_KW.search(line):
                    stripped = line.strip()
                    if stripped and len(stripped) < 200:
                        pending_items.append(stripped)

        # --- Build conversation summary ---
        if role == "tool":
            parts.append(f"[tool result: {content[:50]}...]")
        elif role == "user":
            trimmed = content[:100]
            parts.append(f"User: {trimmed}")
            user_requests.append(trimmed)
        elif role == "assistant":
            if "tool_calls" in msg:
                tools = [tc["function"]["name"] for tc in msg.get("tool_calls", [])]
                for t in tools:
                    tool_counts[t] = tool_counts.get(t, 0) + 1
                parts.append(f"JARVIS called: {', '.join(tools)}")
            elif content:
                parts.append(f"JARVIS: {content[:80]}")

    # --- Assemble enriched summary ---
    sections = []

    # Recent user requests (last 3)
    recent = user_requests[-3:]
    if recent:
        sections.append("Recent requests:\n" + "\n".join(f"  - {r}" for r in recent))

    # Conversation flow
    if parts:
        sections.append("Conversation:\n" + "\n".join(parts))

    # Tool usage summary
    if tool_counts:
        tool_lines = [f"  {name}: {count}x" for name, count in
                       sorted(tool_counts.items(), key=lambda x: -x[1])]
        sections.append("Tools used:\n" + "\n".join(tool_lines))

    # File references
    if file_refs:
        sorted_refs = sorted(file_refs)[:20]  # Cap at 20 to avoid bloat
        sections.append("Files referenced:\n" + "\n".join(f"  {f}" for f in sorted_refs))

    # Pending work
    if pending_items:
        unique_pending = list(dict.fromkeys(pending_items))[:10]  # Dedupe, cap at 10
        sections.append("Pending work:\n" + "\n".join(f"  - {p}" for p in unique_pending))

    return "\n\n".join(sections)


def token_usage_display(messages: list[dict], model: str = "") -> str:
    """Format token usage for the status line."""
    used = estimate_tokens(messages)
    limit = MODEL_LIMITS.get(model, DEFAULT_MAX_TOKENS)
    pct = min(100, int(used / limit * 100))
    bar_len = 10
    filled = int(bar_len * pct / 100)
    bar = "█" * filled + "░" * (bar_len - filled)
    return f"{bar} {used:,}/{limit:,} tokens ({pct}%)"