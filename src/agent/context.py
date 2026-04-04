"""JARVIS Context Manager — prevents context overflow in the agent loop.

Inspired by Claude Code's compaction strategy:
- Adaptive compaction based on model context limits
- Three-phase approach: truncate tool results → summarize → drop
- Preserves critical recent context and system prompt
- Tracks token usage for status display
"""

import copy
import json as _json
import re
from dataclasses import dataclass, field

from src.services.tokenEstimation import (
    rough_token_count_estimation,
    rough_token_count_estimation_for_content,
    rough_token_count_estimation_for_file_type,
    bytes_per_token_for_file_type,
)

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
    """Token count for a message list.

    Uses file-type-aware estimation from the token estimation service
    for tool call arguments (JSON args use 2 bytes/token instead of 4)
    and structured content blocks.
    """
    total = 0
    for msg in messages:
        content = msg.get("content", "") or ""
        if isinstance(content, list):
            # Structured content blocks (Anthropic format)
            total += rough_token_count_estimation_for_content(content)
        else:
            total += len(content) // CHARS_PER_TOKEN
        # Tool calls — arguments are JSON, so use the denser ratio
        if "tool_calls" in msg:
            for tc in msg["tool_calls"]:
                args = tc.get("function", {}).get("arguments", "")
                total += rough_token_count_estimation_for_file_type(args, "json") + 20
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


# ---------------------------------------------------------------------------
# Token Budget Tracking & Auto-Compaction
# ---------------------------------------------------------------------------


@dataclass
class TokenBudget:
    """Tracks token usage against a model's context limit."""

    max_tokens: int
    used_tokens: int = 0
    compaction_count: int = 0
    cumulative_input_tokens: int = 0
    cumulative_output_tokens: int = 0

    @property
    def remaining(self) -> int:
        return self.max_tokens - self.used_tokens

    @property
    def usage_pct(self) -> float:
        if self.max_tokens == 0:
            return 0.0
        return (self.used_tokens / self.max_tokens) * 100

    @property
    def is_critical(self) -> bool:
        return self.usage_pct > 90

    @property
    def needs_compaction(self) -> bool:
        return self.usage_pct > 80


def microcompact_messages(
    messages: list[dict],
    preserve_recent: int = 10,
) -> list[dict]:
    """Lightweight compaction: truncate old tool results only.

    - Only touches tool-role messages older than *preserve_recent* turns
    - Truncates results >500 chars to first 200 + ellipsis + last 100
    - Never mutates the input list
    """
    result: list[dict] = []
    cutoff = max(0, len(messages) - preserve_recent)

    for idx, msg in enumerate(messages):
        if idx < cutoff and msg.get("role") == "tool":
            content = msg.get("content", "") or ""
            if len(content) > 500:
                omitted = len(content) - 300
                truncated = (
                    content[:200]
                    + f"\n... ({omitted} chars omitted)\n"
                    + content[-100:]
                )
                result.append({**msg, "content": truncated})
                continue
        result.append(msg)

    return result


class AutoCompactor:
    """Automatic context compaction with token budget awareness.

    Wraps the existing ``compact_messages`` and the new ``microcompact_messages``
    behind a simple API that the agent loop can call every turn.
    """

    def __init__(self, model: str = "", proactive_threshold: float = 0.75):
        max_tokens = MODEL_LIMITS.get(model, DEFAULT_MAX_TOKENS)
        self.budget = TokenBudget(max_tokens=max_tokens)
        self._proactive_threshold = proactive_threshold
        self._last_compact_tokens: int = 0
        self._microcompact_interval: int = 5
        self._turn_count: int = 0
        self._model = model

    # -- helpers ----------------------------------------------------------

    def update(self, messages: list[dict]) -> None:
        """Recalculate *used_tokens* from the current message list."""
        self.budget.used_tokens = estimate_tokens(messages)

    def should_compact(self, messages: list[dict]) -> bool:
        """Return True when usage exceeds the proactive threshold."""
        self.update(messages)
        return self.budget.usage_pct > (self._proactive_threshold * 100)

    # -- compaction entry points ------------------------------------------

    def maybe_compact(self, messages: list[dict]) -> tuple[list[dict], bool]:
        """Compact only if needed. Returns (messages, did_compact)."""
        self._turn_count += 1
        if self.should_compact(messages):
            compacted = self.auto_compact(messages)
            return compacted, True
        # Periodic microcompact even when under threshold
        if self._turn_count % self._microcompact_interval == 0:
            compacted = self.microcompact(messages)
            if estimate_tokens(compacted) < estimate_tokens(messages):
                self.update(compacted)
                return compacted, True
        return messages, False

    def microcompact(self, messages: list[dict]) -> list[dict]:
        """Lightweight pass: truncate old tool results only."""
        result = microcompact_messages(messages, preserve_recent=10)
        self.update(result)
        return result

    def auto_compact(self, messages: list[dict]) -> list[dict]:
        """Full smart compaction with escalation.

        1. Try microcompact first (cheap).
        2. If still over threshold, escalate to full ``compact_messages``.
        3. Update budget bookkeeping.
        """
        # Stage 1 — microcompact
        result = self.microcompact(messages)

        # Stage 2 — full compaction if still over threshold
        if self.budget.usage_pct > (self._proactive_threshold * 100):
            result = compact_messages(
                result,
                max_tokens=self.budget.max_tokens,
            )
            self.update(result)

        self.budget.compaction_count += 1
        self._last_compact_tokens = self.budget.used_tokens
        return result

    # -- status -----------------------------------------------------------

    def get_budget(self) -> TokenBudget:
        """Return current budget snapshot."""
        return self.budget

    def get_status(self) -> str:
        """Human-readable status line."""
        pct = int(self.budget.usage_pct)
        used_k = self.budget.used_tokens / 1000
        max_k = self.budget.max_tokens / 1000
        compactions = self.budget.compaction_count
        suffix = "compaction" if compactions == 1 else "compactions"
        return f"{pct}% used ({used_k:.0f}K/{max_k:.0f}K) | {compactions} {suffix}"


# ---------------------------------------------------------------------------
# LLM-Based Compaction (ported from Claude Code's compact service)
# ---------------------------------------------------------------------------


@dataclass
class MessageGroup:
    """A logical group of messages forming one conversational unit."""

    messages: list[dict]
    group_type: str  # "system", "user_turn", "agent_turn", "tool_batch"
    token_estimate: int = 0
    is_recent: bool = False

    def __post_init__(self) -> None:
        if self.token_estimate == 0:
            self.token_estimate = estimate_tokens(self.messages)


def group_messages_by_turn(messages: list[dict]) -> list[MessageGroup]:
    """Group messages into logical turns, preserving tool_call/tool_result pairs.

    Grouping rules:
    - System message -> its own "system" group
    - User message -> starts a new "user_turn" group
    - Assistant message with tool_calls + following tool results -> "agent_turn"
    - Assistant message (text only) -> appended to the current "user_turn"

    This is better than arbitrary indexing because it keeps tool_call and
    tool_result messages together, which is required by most LLM APIs.
    """
    groups: list[MessageGroup] = []
    current_msgs: list[dict] = []
    current_type: str = "user_turn"

    def _flush() -> None:
        nonlocal current_msgs, current_type
        if current_msgs:
            groups.append(MessageGroup(
                messages=list(current_msgs),
                group_type=current_type,
            ))
            current_msgs = []
            current_type = "user_turn"

    for msg in messages:
        role = msg.get("role", "")

        # System messages are always their own group
        if role == "system":
            _flush()
            groups.append(MessageGroup(
                messages=[msg],
                group_type="system",
            ))
            continue

        # User message starts a new user_turn group
        if role == "user":
            _flush()
            current_msgs = [msg]
            current_type = "user_turn"
            continue

        # Assistant with tool_calls -> start an agent_turn group
        if role == "assistant" and msg.get("tool_calls"):
            _flush()
            current_msgs = [msg]
            current_type = "agent_turn"
            continue

        # Tool result -> belongs with the preceding agent_turn
        if role == "tool":
            if current_type != "agent_turn":
                # Orphaned tool result; wrap in a tool_batch
                _flush()
                current_type = "tool_batch"
            current_msgs.append(msg)
            continue

        # Assistant text-only -> append to current group (closes a user_turn)
        if role == "assistant":
            current_msgs.append(msg)
            _flush()
            continue

        # Fallback: append to current group
        current_msgs.append(msg)

    _flush()
    return groups


@dataclass
class CompactionResult:
    """Result of a smart compaction operation."""

    messages: list[dict]
    summary: str
    tokens_before: int
    tokens_after: int
    groups_removed: int


def build_compaction_prompt(
    groups: list[MessageGroup],
    preserve_recent: int = 3,
) -> str:
    """Build a prompt asking an LLM to summarize old conversation groups.

    Only formats groups that are NOT in the recent window (i.e. groups
    whose ``is_recent`` flag is False). The resulting prompt instructs
    the model to retain the most useful contextual information.
    """
    old_groups = [g for g in groups if not g.is_recent]
    if not old_groups:
        return ""

    formatted_blocks: list[str] = []
    for idx, group in enumerate(old_groups):
        lines: list[str] = [f"--- {group.group_type} (group {idx + 1}) ---"]
        for msg in group.messages:
            role = msg.get("role", "unknown")
            content = (msg.get("content", "") or "")[:2000]
            if role == "assistant" and msg.get("tool_calls"):
                tool_names = [
                    tc.get("function", {}).get("name", "?")
                    for tc in msg["tool_calls"]
                ]
                lines.append(f"[assistant] called tools: {', '.join(tool_names)}")
            elif role == "tool":
                # Truncate large tool results in the prompt itself
                if len(content) > 500:
                    content = content[:300] + " ... (truncated)"
                lines.append(f"[tool] {content}")
            else:
                lines.append(f"[{role}] {content}")
        formatted_blocks.append("\n".join(lines))

    conversation_text = "\n\n".join(formatted_blocks)

    # Use the richer prompt template from the compact service which
    # instructs the LLM to produce <analysis> + <summary> blocks with
    # better preservation of file paths, code snippets, and error context.
    from src.services.compact.prompt import build_compact_prompt
    base_prompt = build_compact_prompt(direction="full")

    return (
        f"{base_prompt}\n\n"
        "Conversation to summarize:\n"
        f"{conversation_text}"
    )


async def smart_compact(
    messages: list[dict],
    max_tokens: int,
    summarizer=None,
    preserve_recent: int = 3,
) -> CompactionResult:
    """Smart compaction that optionally uses an LLM for summarization.

    Steps:
    1. Group messages by turn.
    2. Mark the last *preserve_recent* groups as recent.
    3. If total tokens are under budget, return unchanged.
    4. If a *summarizer* callable is provided, use LLM-based summarization.
    5. Otherwise fall back to heuristic ``build_context_summary()``.

    Parameters
    ----------
    messages:
        Full message list (including system prompt).
    max_tokens:
        Token budget to target.
    summarizer:
        Optional async callable ``summarizer(prompt: str) -> str`` that
        calls an LLM and returns a summary string.
    preserve_recent:
        Number of most-recent groups to keep verbatim.
    """
    tokens_before = estimate_tokens(messages)

    # Under budget — nothing to do
    if tokens_before <= max_tokens:
        return CompactionResult(
            messages=messages,
            summary="",
            tokens_before=tokens_before,
            tokens_after=tokens_before,
            groups_removed=0,
        )

    groups = group_messages_by_turn(messages)

    # Mark recent groups
    non_system = [g for g in groups if g.group_type != "system"]
    for g in non_system[-preserve_recent:]:
        g.is_recent = True

    old_groups = [g for g in groups if not g.is_recent and g.group_type != "system"]
    system_groups = [g for g in groups if g.group_type == "system"]
    recent_groups = [g for g in groups if g.is_recent]

    # --- Generate summary ---
    summary = ""
    if summarizer is not None:
        prompt = build_compaction_prompt(groups, preserve_recent)
        if prompt:
            try:
                raw_summary = await summarizer(prompt)
                # The compact prompt asks for <analysis>+<summary> blocks;
                # extract just the <summary> content for cleaner storage.
                from src.services.compact.compact import format_compact_summary
                summary = format_compact_summary(raw_summary)
            except Exception:
                # Fall back to heuristic on any error
                old_msgs = [m for g in old_groups for m in g.messages]
                summary = build_context_summary(old_msgs)
    else:
        old_msgs = [m for g in old_groups for m in g.messages]
        summary = build_context_summary(old_msgs)

    # Build attachments from the compacted region
    old_msgs = [m for g in old_groups for m in g.messages]
    attachments = get_compaction_attachments(old_msgs)
    if attachments:
        summary = summary + "\n\n" + attachments

    # Reassemble: system + summary + recent
    system_msgs = [m for g in system_groups for m in g.messages]
    recent_msgs = [m for g in recent_groups for m in g.messages]

    summary_msg = {
        "role": "user",
        "content": f"[Conversation summary from compaction]\n{summary}",
    }

    compacted = system_msgs + [summary_msg] + recent_msgs
    tokens_after = estimate_tokens(compacted)

    return CompactionResult(
        messages=compacted,
        summary=summary,
        tokens_before=tokens_before,
        tokens_after=tokens_after,
        groups_removed=len(old_groups),
    )


def get_compaction_attachments(messages: list[dict]) -> str:
    """Generate an attachments block describing activity in the compacted region.

    Extracts from tool calls in the compacted messages:
    - Files modified (write_file / edit_file calls)
    - Files read (read_file calls)
    - Per-tool invocation counts

    Returns a compact markdown string, or empty string if nothing notable.
    """
    files_modified: set[str] = set()
    files_read: set[str] = set()
    tool_counts: dict[str, int] = {}

    for msg in messages:
        if msg.get("role") != "assistant" or "tool_calls" not in msg:
            continue
        for tc in msg["tool_calls"]:
            func = tc.get("function", {})
            name = func.get("name", "")
            args_str = func.get("arguments", "")

            tool_counts[name] = tool_counts.get(name, 0) + 1

            # Try to extract path from arguments
            path = ""
            if '"path"' in args_str or '"file_path"' in args_str:
                # Quick regex extraction rather than JSON parse (arguments
                # may be malformed in edge cases)
                m = re.search(r'"(?:path|file_path)"\s*:\s*"([^"]+)"', args_str)
                if m:
                    path = m.group(1)

            if name in ("write_file", "edit_file") and path:
                files_modified.add(path)
            elif name == "read_file" and path:
                files_read.add(path)

    if not tool_counts:
        return ""

    sections: list[str] = ["**Compacted region activity:**"]

    if files_modified:
        sections.append("Files modified:\n" + "\n".join(
            f"  - {f}" for f in sorted(files_modified)
        ))

    if files_read:
        sections.append("Files read:\n" + "\n".join(
            f"  - {f}" for f in sorted(files_read)[:20]
        ))

    tool_lines = [f"  {name}: {count}x" for name, count in
                  sorted(tool_counts.items(), key=lambda x: -x[1])]
    sections.append("Tool usage:\n" + "\n".join(tool_lines))

    return "\n".join(sections)


def format_token_budget_status(
    budget_or_messages,
    model: str = "",
) -> dict:
    """Analyze context token usage and return a structured status dict.

    Parameters
    ----------
    budget_or_messages:
        Either a ``list[dict]`` of messages or a ``TokenBudget`` instance.
    model:
        Model name for looking up the context limit.

    Returns a dict with keys: total_tokens, max_tokens, usage_pct,
    breakdown (system_prompt, conversation, tool_results, recent_context),
    and recommendation ("ok", "consider_compacting", "compact_now", "critical").
    """
    if isinstance(budget_or_messages, TokenBudget):
        total_tokens = budget_or_messages.used_tokens
        max_tokens = budget_or_messages.max_tokens
        # Can't compute breakdown without messages
        breakdown = {
            "system_prompt": 0,
            "conversation": total_tokens,
            "tool_results": 0,
            "recent_context": 0,
        }
    else:
        messages: list[dict] = budget_or_messages
        max_tokens = MODEL_LIMITS.get(model, DEFAULT_MAX_TOKENS)

        system_tokens = 0
        conversation_tokens = 0
        tool_tokens = 0
        recent_tokens = 0

        # Last 6 messages are considered "recent context"
        recent_cutoff = max(0, len(messages) - 6)

        for idx, msg in enumerate(messages):
            content = msg.get("content", "") or ""
            msg_tokens = len(content) // CHARS_PER_TOKEN
            # Add overhead for tool calls
            if "tool_calls" in msg:
                for tc in msg["tool_calls"]:
                    args = tc.get("function", {}).get("arguments", "")
                    msg_tokens += len(args) // CHARS_PER_TOKEN + 20

            role = msg.get("role", "")
            if role == "system":
                system_tokens += msg_tokens
            elif role == "tool":
                tool_tokens += msg_tokens
            elif idx >= recent_cutoff:
                recent_tokens += msg_tokens
            else:
                conversation_tokens += msg_tokens

        total_tokens = system_tokens + conversation_tokens + tool_tokens + recent_tokens
        breakdown = {
            "system_prompt": system_tokens,
            "conversation": conversation_tokens,
            "tool_results": tool_tokens,
            "recent_context": recent_tokens,
        }

    usage_pct = (total_tokens / max_tokens * 100) if max_tokens > 0 else 0.0

    if usage_pct > 90:
        recommendation = "critical"
    elif usage_pct > 80:
        recommendation = "compact_now"
    elif usage_pct > 60:
        recommendation = "consider_compacting"
    else:
        recommendation = "ok"

    return {
        "total_tokens": total_tokens,
        "max_tokens": max_tokens,
        "usage_pct": round(usage_pct, 1),
        "breakdown": breakdown,
        "recommendation": recommendation,
    }