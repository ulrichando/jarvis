"""JARVIS Assistant — core assistant configuration and response logic.

Provides assistant config, system prompt construction, tool-use classification,
thinking extraction, and response formatting.

Brain.think() remains the main orchestrator — this module provides building
blocks that Brain (and agent loop) can call without duplicating logic.
"""

import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("jarvis.assistant")


# ── Assistant Configuration ──────────────────────────────────────────


@dataclass
class AssistantConfig:
    """Configuration for the assistant's behavior on a per-request basis."""
    model: str = ""                     # Active model name/id
    system_prompt: str = ""             # Base system prompt (overridden by build_system_prompt)
    max_tokens: int = 16384             # Max output tokens
    temperature: float = 0.7            # Sampling temperature
    tools_enabled: bool = True          # Whether tool calling is available
    effort: str = "standard"            # Reasoning effort: "low", "standard", "high"
    stream: bool = True                 # Whether to stream responses
    thinking_budget: int = 0            # Token budget for extended thinking (0 = off)
    context_window: int = 200000        # Context window size for the active model


# ── System Prompt Construction ───────────────────────────────────────

# Capability sections appended to the system prompt when relevant.
_TOOL_CAPABILITIES = """
You have access to tools for reading files, writing files, running commands,
searching code, and browsing the web. Use them to investigate before answering.
"""

_COORDINATOR_CAPABILITIES = """
You can spawn sub-agents (scout, worker, planner) to handle parallel tasks.
Delegate research and implementation to workers; synthesize their results.
"""

_MCP_CAPABILITIES_TEMPLATE = """
External tool servers are connected via MCP: {servers}.
Their tools are available alongside the built-in tools.
"""


def build_system_prompt(
    config: AssistantConfig,
    context: dict[str, Any] | None = None,
    capabilities: list[str] | None = None,
) -> str:
    """Construct the full system prompt from config, runtime context, and capabilities.

    Args:
        config: Assistant configuration (model, effort, etc.)
        context: Runtime context dict with keys like 'cwd', 'jarvis_root',
                 'model_name', 'mcp_servers', 'mode', 'date'.
        capabilities: Optional list of capability names to include:
                      'tools', 'coordinator', 'mcp'.

    Returns:
        Complete system prompt string ready to send to the model.
    """
    context = context or {}
    capabilities = capabilities or []

    # Start with the base prompt from config (identity + personality).
    # Brain.AGENT_SYSTEM_PROMPT is typically set as config.system_prompt.
    prompt = config.system_prompt

    # Substitute runtime placeholders if present
    substitutions = {
        "{cwd}": context.get("cwd", os.getcwd()),
        "{jarvis_root}": context.get("jarvis_root", ""),
        "{model_name}": context.get("model_name", config.model),
        "{date}": context.get("date", time.strftime("%Y-%m-%d")),
    }
    for placeholder, value in substitutions.items():
        prompt = prompt.replace(placeholder, str(value))

    # Append capability sections
    if "tools" in capabilities:
        prompt += "\n" + _TOOL_CAPABILITIES.strip()
    if "coordinator" in capabilities:
        prompt += "\n" + _COORDINATOR_CAPABILITIES.strip()
    if "mcp" in capabilities:
        servers = context.get("mcp_servers", "")
        if servers:
            prompt += "\n" + _MCP_CAPABILITIES_TEMPLATE.format(servers=servers).strip()

    # Effort-based instructions
    if config.effort == "low":
        prompt += "\n\nBe concise. Skip detailed explanations unless asked."
    elif config.effort == "high":
        prompt += "\n\nThink step by step. Be thorough. Show your reasoning."

    return prompt


# ── Tool-Use Classification ─────────────────────────────────────────

# Patterns that almost always require tool use
_TOOL_PATTERNS = [
    # File operations
    r"\b(read|open|show|cat|view|look at|check)\b.+\.(py|js|ts|rs|go|java|c|cpp|h|md|txt|json|yaml|toml|cfg|ini|sh|bash|zsh)\b",
    r"\b(write|create|save|edit|modify|update|change|fix|patch|add|remove|delete|rename)\b.+\b(file|code|function|class|module|config)\b",
    # Shell / system
    r"\b(run|execute|install|pip|npm|cargo|apt|brew|git|docker|make|cmake|curl|wget)\b",
    r"\b(ls|cd|mkdir|rm|cp|mv|chmod|chown|ps|kill|top|df|du|grep|find|sed|awk)\b",
    # Search
    r"\b(search|find|grep|look for|where is|locate)\b.+\b(in|across|through|the)\b",
    # Web
    r"\b(search the web|google|look up online|fetch|browse|scrape|download)\b",
    # Code analysis that requires reading
    r"\b(review|audit|analyze|inspect|debug|trace|profile)\b.+\b(code|codebase|repo|project|module)\b",
    # Explicit tool requests
    r"\b(use|call|invoke|run)\b.+\b(tool|command|bash|terminal|shell)\b",
]

# Patterns that almost never need tools (pure conversation)
_NO_TOOL_PATTERNS = [
    r"^(hi|hey|hello|yo|sup|what'?s up|how are you|good morning|good evening)\b",
    r"^(thanks|thank you|thx|ty|cheers|nice|cool|ok|okay|got it|sure|yep|nope)\b",
    r"\b(explain|what is|what are|how does|why does|tell me about|describe|define)\b.+\b(concept|theory|algorithm|pattern|principle|idea)\b",
    r"^(who|what|when|where|why|how)\b.+\?$",  # Simple questions
    r"\b(joke|story|poem|haiku|limerick|riddle)\b",
    r"\b(opinion|think|feel|believe|prefer|recommend)\b",
]


def should_use_tools(user_input: str, history: list[dict] | None = None) -> bool:
    """Classify whether a user query likely needs tool use.

    This is a fast heuristic classifier that runs BEFORE any LLM call.
    It checks patterns in the input text and recent history to decide
    whether to enter the agent loop (with tools) or give a standard
    LLM response (no tools).

    Args:
        user_input: The user's message text.
        history: Optional recent conversation history (list of role/content dicts).

    Returns:
        True if the query likely needs tools, False for plain conversation.
    """
    text = user_input.strip()
    if not text:
        return False

    lower = text.lower()

    # Explicit opt-in/out
    if lower.startswith("use tools") or lower.startswith("agent:"):
        return True
    if lower.startswith("no tools") or lower.startswith("just chat"):
        return False

    # Check tool-needing patterns
    for pattern in _TOOL_PATTERNS:
        if re.search(pattern, lower):
            return True

    # Check no-tool patterns
    for pattern in _NO_TOOL_PATTERNS:
        if re.search(pattern, lower):
            return False

    # History heuristic: if the last assistant message used tools, the user
    # is probably continuing that workflow.
    if history:
        for msg in reversed(history[-5:]):
            if msg.get("role") == "assistant":
                content = msg.get("content", "")
                if isinstance(content, list):
                    # Check for tool_use blocks in structured content
                    if any(
                        isinstance(b, dict) and b.get("type") == "tool_use"
                        for b in content
                    ):
                        return True
                break

    # Length heuristic: very long messages are usually pasting code/logs
    # that need analysis with tools.
    if len(text) > 500:
        return True

    # Default: no tools (standard LLM response)
    return False


# ── Thinking Extraction ──────────────────────────────────────────────


def extract_thinking(response: dict) -> str:
    """Extract thinking/reasoning content from an LLM response.

    Supports multiple formats:
    - Anthropic extended thinking blocks (type: 'thinking')
    - <thinking>...</thinking> XML tags in text
    - OpenAI-style reasoning_content field

    Args:
        response: Raw response dict from the LLM provider.

    Returns:
        Extracted thinking text, or empty string if none found.
    """
    # Anthropic extended thinking
    content = response.get("content", [])
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "thinking":
                return block.get("thinking", "")

    # OpenAI reasoning_content
    reasoning = response.get("reasoning_content", "")
    if reasoning:
        return reasoning

    # XML-tag thinking in text content
    text = ""
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = " ".join(
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )

    match = re.search(r"<thinking>(.*?)</thinking>", text, re.DOTALL)
    if match:
        return match.group(1).strip()

    return ""


# ── Response Formatting ──────────────────────────────────────────────


def format_response(text: str, thinking: str = "") -> str:
    """Format the final response with optional thinking display.

    When thinking is present, it is prepended in a collapsed block
    that CLI/web shells can render appropriately.

    Args:
        text: The main response text.
        thinking: Optional thinking/reasoning to display.

    Returns:
        Formatted response string.
    """
    if not thinking:
        return text.strip()

    # Truncate very long thinking for display
    display_thinking = thinking
    if len(display_thinking) > 2000:
        display_thinking = display_thinking[:2000] + "\n... (truncated)"

    return f"<details><summary>Thinking</summary>\n\n{display_thinking}\n\n</details>\n\n{text.strip()}"


# ── Session History (ported from sessionHistory.ts) ───────────────────

HISTORY_PAGE_SIZE = 100


@dataclass
class HistoryPage:
    """A page of session history events."""
    events: list[dict] = field(default_factory=list)
    first_id: str | None = None   # Oldest event ID (cursor for older page)
    has_more: bool = False         # True if older events exist


@dataclass
class HistoryAuthContext:
    """Auth context for fetching session history from a remote API."""
    base_url: str = ""
    headers: dict[str, str] = field(default_factory=dict)


async def fetch_latest_events(
    ctx: HistoryAuthContext,
    limit: int = HISTORY_PAGE_SIZE,
) -> HistoryPage | None:
    """Fetch the newest page of session events.

    Args:
        ctx: Authentication context with base_url and headers.
        limit: Maximum number of events to fetch.

    Returns:
        HistoryPage or None on error.
    """
    try:
        import aiohttp
        params = {"limit": limit, "anchor_to_latest": True}
        async with aiohttp.ClientSession() as session:
            async with session.get(
                ctx.base_url, headers=ctx.headers, params=params, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status != 200:
                    log.debug("fetchLatestEvents HTTP %d", resp.status)
                    return None
                data = await resp.json()
                return HistoryPage(
                    events=data.get("data", []),
                    first_id=data.get("first_id"),
                    has_more=data.get("has_more", False),
                )
    except Exception as e:
        log.debug("fetchLatestEvents error: %s", e)
        return None


async def fetch_older_events(
    ctx: HistoryAuthContext,
    before_id: str,
    limit: int = HISTORY_PAGE_SIZE,
) -> HistoryPage | None:
    """Fetch a page of events older than the given cursor.

    Args:
        ctx: Authentication context.
        before_id: Event ID cursor; fetch events before this.
        limit: Maximum number of events.

    Returns:
        HistoryPage or None on error.
    """
    try:
        import aiohttp
        params = {"limit": limit, "before_id": before_id}
        async with aiohttp.ClientSession() as session:
            async with session.get(
                ctx.base_url, headers=ctx.headers, params=params, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status != 200:
                    log.debug("fetchOlderEvents HTTP %d", resp.status)
                    return None
                data = await resp.json()
                return HistoryPage(
                    events=data.get("data", []),
                    first_id=data.get("first_id"),
                    has_more=data.get("has_more", False),
                )
    except Exception as e:
        log.debug("fetchOlderEvents error: %s", e)
        return None
