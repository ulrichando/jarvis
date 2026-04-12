"""
Request classifier — routes each message to the right model and tool.
Runs entirely locally with zero model calls, zero latency, zero cost.
"""

import hashlib
import re
from dataclasses import dataclass
from typing import Literal

# ── Qwen regex patterns (checked BEFORE complex-signal detection) ─────────────
# These intercept simple factual / conversational requests that would otherwise
# fall through to Claude when the exact phrase isn't in _GREETING_WORDS.
_QWEN_PATTERNS: list[re.Pattern] = [re.compile(p, re.IGNORECASE) for p in [
    # Factual look-ups answerable from training data — no tool needed
    r"^(what is|what's|what are|what were|what was)\b",
    r"^(who is|who's|who are|who was|who were)\b",
    r"^(where is|where's|where are|where was)\b",
    r"^(when (is|was|were|did|does|do))\b",
    r"^(how (many|much|long|far|old|tall|big|fast))\b",
    r"^(why (is|was|are|were|do|does|did))\b",
    r"^(define|meaning of|what does .+ mean)\b",
    # Status / capability questions
    r"^(are you|can you|do you|is jarvis|does jarvis)\b",
    # Very short single-token input that passed the voice gate
    r"^\w{1,15}$",
]]


@dataclass
class Classification:
    route_to: Literal["qwen", "claude", "inline"]
    needs_tool: bool
    tool_name: str | None        # exact tool name if known, else None
    max_tokens: int              # token budget for this request
    cache_key: str | None        # set if this request is cacheable
    task_type: str               # "simple" | "tool" | "complex"


# ── Token budgets ─────────────────────────────────────────────────────────────

MAX_TOKENS_BY_TASK: dict[str, int] = {
    "simple":  150,
    "tool":    300,
    "complex": 800,
}

MAX_TOKENS_BY_CHANNEL: dict[str, int] = {
    "voice":  100,   # HARD CAP — TTS reads every word
    "chrome": 200,
    "cli":    800,
}

# ── Tool keyword triggers ─────────────────────────────────────────────────────

TOOL_TRIGGERS: dict[str, list[str]] = {
    "get_weather": [
        "weather", "temperature", "rain", "sunny", "cloudy",
        "forecast", "hot outside", "cold outside", "jacket",
        "umbrella", "météo", "temps qu'il fait",
    ],
    "play_music": [
        "play", "music", "song", "track", "playlist", "put on",
        "jouer", "musique",
    ],
    "set_reminder": [
        "remind me", "reminder", "alarm", "alert me", "don't let me forget",
        "rappelle moi", "rappel",
    ],
    "web_search": [
        "search for", "look up", "find me", "google", "latest news",
        "what happened", "current", "news about", "recherche",
    ],
    "open_app": [
        "open", "launch", "start", "ouvre", "lance",
    ],
    "system_control": [
        "volume", "brightness", "mute", "screenshot", "lock screen",
        "shutdown", "restart", "sleep",
    ],
}

# ── Keyword sets for routing ──────────────────────────────────────────────────

_GREETING_WORDS = {
    "hello", "hey", "hi", "good morning", "good evening", "good afternoon",
    "bonjour", "salut", "bonsoir", "howdy", "sup",
}

_CLOSING_WORDS = {
    "thanks", "thank you", "merci", "goodbye", "bye", "ok", "okay",
    "got it", "understood", "cool", "great", "perfect", "noted",
    "d'accord", "c'est bon",
}

_COMPLEX_SIGNALS = {
    "explain", "analyze", "analyse", "compare", "write", "draft",
    "generate", "plan", "design", "code", "implement", "refactor",
    "debug", "review", "summarize", "translate", "legal", "financial",
    "contract", "law", "tax", "audit", "security",
}

_CACHEABLE_TOOLS = {"get_weather", "web_search"}
_CACHEABLE_ROUTES = {"inline"}


def detect_tool(message: str) -> str | None:
    """Return the matching tool name if message contains a trigger keyword, else None."""
    msg = message.lower()
    for tool_name, keywords in TOOL_TRIGGERS.items():
        if any(kw in msg for kw in keywords):
            return tool_name
    return None


def _make_cache_key(channel_id: str, message: str) -> str:
    """Stable MD5 cache key from channel + normalised message."""
    normalized = f"{channel_id}:{message.lower().strip()}"
    return hashlib.md5(normalized.encode()).hexdigest()


def classify_request(message: str, channel_id: str) -> Classification:
    """
    Classify a user message and determine routing, tool, and token budget.
    Runs entirely locally — no model call, no network, no latency.

    Routing priority (applied in order):
      1. inline  — answer directly from system state (time, date, status)
      2. qwen    — free local model for greetings, closings, simple Q&A
      3. claude  — paid API, only for tool calls and complex reasoning
    """
    msg = message.lower().strip()
    words = msg.split()

    # ── 1. Inline triggers ────────────────────────────────────────────────────
    _time_kw   = ["what time", "current time", "quelle heure", "what's the time"]
    _date_kw   = ["what day", "today's date", "what date", "quelle date", "what is today"]
    _status_kw = ["are you online", "are you there", "you there", "jarvis online"]

    if any(t in msg for t in _time_kw + _date_kw + _status_kw):
        return Classification(
            route_to="inline",
            needs_tool=False,
            tool_name=None,
            max_tokens=20,
            cache_key=None,   # inline answers are computed live — never cache
            task_type="simple",
        )

    # ── 2. Tool detection (→ claude) ─────────────────────────────────────────
    tool = detect_tool(message)
    if tool:
        task_budget    = MAX_TOKENS_BY_TASK["tool"]
        channel_budget = MAX_TOKENS_BY_CHANNEL.get(channel_id, 800)
        cache_key      = _make_cache_key(channel_id, message) if tool in _CACHEABLE_TOOLS else None
        return Classification(
            route_to="claude",
            needs_tool=True,
            tool_name=tool,
            max_tokens=min(task_budget, channel_budget),
            cache_key=cache_key,
            task_type="tool",
        )

    # ── 3a. Simple factual patterns (→ qwen) — checked before complex signals ──
    # Catches "what is X", "who was X", "how many X" etc. that don't need Claude.
    if any(p.match(msg) for p in _QWEN_PATTERNS):
        channel_budget = MAX_TOKENS_BY_CHANNEL.get(channel_id, 800)
        return Classification(
            route_to="qwen",
            needs_tool=False,
            tool_name=None,
            max_tokens=min(MAX_TOKENS_BY_TASK["simple"], channel_budget),
            cache_key=_make_cache_key(channel_id, message),
            task_type="simple",
        )

    # ── 3b. Complex reasoning signals (→ claude) ──────────────────────────────
    if any(sig in msg for sig in _COMPLEX_SIGNALS):
        task_budget    = MAX_TOKENS_BY_TASK["complex"]
        channel_budget = MAX_TOKENS_BY_CHANNEL.get(channel_id, 800)
        return Classification(
            route_to="claude",
            needs_tool=False,
            tool_name=None,
            max_tokens=min(task_budget, channel_budget),
            cache_key=None,
            task_type="complex",
        )

    # ── 4. Greetings / closings (→ qwen) ─────────────────────────────────────
    if msg in _GREETING_WORDS or msg in _CLOSING_WORDS:
        channel_budget = MAX_TOKENS_BY_CHANNEL.get(channel_id, 800)
        return Classification(
            route_to="qwen",
            needs_tool=False,
            tool_name=None,
            max_tokens=min(MAX_TOKENS_BY_TASK["simple"], channel_budget),
            cache_key=_make_cache_key(channel_id, message),
            task_type="simple",
        )

    # ── 5. Voice short requests (→ qwen) ─────────────────────────────────────
    if channel_id == "voice" and len(words) <= 3:
        return Classification(
            route_to="qwen",
            needs_tool=False,
            tool_name=None,
            max_tokens=MAX_TOKENS_BY_CHANNEL["voice"],
            cache_key=_make_cache_key(channel_id, message),
            task_type="simple",
        )

    # ── 6. General simple Q&A (→ qwen) ───────────────────────────────────────
    channel_budget = MAX_TOKENS_BY_CHANNEL.get(channel_id, 800)
    return Classification(
        route_to="qwen",
        needs_tool=False,
        tool_name=None,
        max_tokens=min(MAX_TOKENS_BY_TASK["simple"], channel_budget),
        cache_key=_make_cache_key(channel_id, message),
        task_type="simple",
    )
