"""JARVIS Reasoner — unified multi-provider backend.

Routes queries through the ProviderRegistry, which manages all AI
backends (Claude, Groq, OpenAI, local models, user-added APIs).

The GroqReasoner name is kept for backward compatibility with the
rest of the codebase that imports it.
"""

import logging
import time
import re
from brain.reasoning.providers import ProviderRegistry

log = logging.getLogger("jarvis.reasoning")

# Built-in knowledge so JARVIS can answer basic questions without any AI provider
_LOCAL_KNOWLEDGE = {
    "alphabet": "The English alphabet has 26 letters: A, B, C, D, E, F, G, H, I, J, K, L, M, N, O, P, Q, R, S, T, U, V, W, X, Y, Z.",
    "numbers": "Numbers are symbols used for counting and measuring. The basic digits are: 0, 1, 2, 3, 4, 5, 6, 7, 8, 9. All other numbers are built from these.",
    "colors": "The primary colors are red, blue, and yellow. Mixing them gives secondary colors: orange, green, and purple.",
    "days": "The days of the week are: Monday, Tuesday, Wednesday, Thursday, Friday, Saturday, Sunday.",
    "months": "The 12 months are: January, February, March, April, May, June, July, August, September, October, November, December.",
    "planets": "The 8 planets in our solar system, from the Sun outward: Mercury, Venus, Earth, Mars, Jupiter, Saturn, Uranus, Neptune.",
    "hello": "Hello! I'm JARVIS. I can answer basic questions, but for deeper conversations I need an AI provider. You can add one by saying 'I have an API key' or through settings.",
    "who are you": "I'm JARVIS — Just A Rather Very Intelligent System. Your personal AI assistant.",
    "time": None,  # handled dynamically
    "date": None,  # handled dynamically
}

_LOCAL_PATTERNS = [
    (r"\b(alphabet|abcs?|letters)\b", "alphabet"),
    (r"\b(count|numbers?|digits?)\b", "numbers"),
    (r"\b(colors?|colours?)\b", "colors"),
    (r"\b(days?\s*(of|in)\s*(the\s*)?week)\b", "days"),
    (r"\b(months?\s*(of|in)\s*(the\s*)?year)\b", "months"),
    (r"\b(planets?|solar\s*system)\b", "planets"),
    (r"^(hi|hello|hey|greetings)\b", "hello"),
    (r"\bwho\s*(are\s*you|is\s*jarvis)\b", "who are you"),
    (r"\bwhat\s*time\b", "time"),
    (r"\bwhat('?s|\s+is)\s*(the\s*)?date\b", "date"),
]


def _local_answer(user_input: str) -> str | None:
    """Try to answer from built-in knowledge. Returns None if no match."""
    text = user_input.lower().strip()
    for pattern, key in _LOCAL_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            if key == "time":
                from datetime import datetime
                return f"It's currently {datetime.now().strftime('%I:%M %p')}."
            if key == "date":
                from datetime import datetime
                return f"Today is {datetime.now().strftime('%A, %B %d, %Y')}."
            return _LOCAL_KNOWLEDGE.get(key)
    return None


class GroqReasoner:
    """Unified reasoner backed by ProviderRegistry."""

    # Cost per million tokens by model (USD)
    MODEL_COSTS = {
        "claude-opus-4-6-20250514": {"input": 15.0, "output": 75.0},
        "claude-sonnet-4-20250514": {"input": 3.0, "output": 15.0},
        "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.0},
        "gpt-4o": {"input": 2.50, "output": 10.0},
        "gpt-4o-mini": {"input": 0.15, "output": 0.60},
        "deepseek-chat": {"input": 0.27, "output": 1.10},
        "deepseek-reasoner": {"input": 0.55, "output": 2.19},
    }

    def __init__(self):
        self.providers = ProviderRegistry()
        self._active_model = "none"
        self._last_latency_ms = 0
        # Session cost tracking
        self.session_input_tokens = 0
        self.session_output_tokens = 0
        self.session_cost_usd = 0.0
        self.session_calls = 0

    def _track_usage(self, usage: dict, model: str = ""):
        """Track token usage and calculate cost with cache savings."""
        inp = usage.get("input", 0)
        out = usage.get("output", 0)
        cache_read = usage.get("cache_read", 0)
        cache_creation = usage.get("cache_creation", 0)
        self.session_input_tokens += inp
        self.session_output_tokens += out
        self.session_calls += 1

        # Calculate cost — cached reads are 90% cheaper
        model_key = model.split(":")[0] if ":" in model else model
        costs = self.MODEL_COSTS.get(model_key, {"input": 1.0, "output": 5.0})
        # Non-cached input + cache reads at 10% + cache creation at 125% + output
        uncached_input = max(0, inp - cache_read - cache_creation)
        cost = (
            uncached_input * costs["input"] +
            cache_read * costs["input"] * 0.1 +
            cache_creation * costs["input"] * 1.25 +
            out * costs["output"]
        ) / 1_000_000
        self.session_cost_usd += cost

    @property
    def usage_stats(self) -> dict:
        return {
            "input_tokens": self.session_input_tokens,
            "output_tokens": self.session_output_tokens,
            "total_tokens": self.session_input_tokens + self.session_output_tokens,
            "cost_usd": round(self.session_cost_usd, 6),
            "calls": self.session_calls,
            "model": self._active_model,
        }

    @property
    def model(self) -> str:
        return self._active_model

    @property
    def active_model_name(self) -> str:
        return self._active_model

    async def query(
        self,
        user_input: str,
        system_prompt: str,
        history: list[dict] | None = None,
    ) -> str:
        """Query best available provider."""
        start = time.time()
        try:
            result, provider_name = await self.providers.query(
                user_input, system_prompt, history,
            )
        except Exception as e:
            log.error("Provider query failed: %s", e)
            result, provider_name = None, "error"
        self._active_model = provider_name
        self._last_latency_ms = int((time.time() - start) * 1000)

        if not result:
            # Try built-in knowledge before giving up
            local = _local_answer(user_input)
            if local:
                self._active_model = "local"
                return local
            return "No AI provider available. Add one: say 'I have an API key' or open settings."
        return result

    async def query_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
    ) -> dict:
        """Tool-calling query through providers."""
        try:
            result, provider_name = await self.providers.query_with_tools(
                messages, tools,
            )
        except Exception as e:
            log.error("Provider tool query failed: %s", e)
            raise
        self._active_model = provider_name
        # Track usage
        if result.get("usage"):
            self._track_usage(result["usage"], provider_name)
        return result

    async def query_stream(
        self,
        user_input: str,
        system_prompt: str,
        history: list[dict] | None = None,
    ):
        """Stream text chunks from the best available provider."""
        start = time.time()
        had_output = False
        try:
            async for chunk in self.providers.query_stream(
                user_input, system_prompt, history,
            ):
                had_output = True
                yield chunk
        except Exception as e:
            log.error("Provider stream query failed: %s", e)
            yield f"Error: {e}"
            return
        self._last_latency_ms = int((time.time() - start) * 1000)

        if not had_output:
            # Fall back to built-in knowledge
            local = _local_answer(user_input)
            if local:
                self._active_model = "local"
                yield local
            else:
                yield "No AI provider available. Add one: say 'I have an API key' or open settings."

    def status(self) -> dict:
        """Return current reasoner status."""
        providers = self.providers.list_providers()
        return {
            "active_model": self._active_model,
            "last_latency_ms": self._last_latency_ms,
            "providers": len(providers),
            "provider_list": [p["name"] for p in providers],
        }
