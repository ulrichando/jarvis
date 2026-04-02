"""Query router — everything goes local (Ollama), Groq only as fallback.

The GroqReasoner already handles Ollama-first routing internally,
so this router is now a thin wrapper for backward compatibility.
"""

from brain.reasoning.groq_client import GroqReasoner


class QueryRouter:
    """Routes all queries through the unified reasoner (Ollama-first)."""

    def __init__(self):
        self.groq = GroqReasoner()

    async def route(
        self,
        user_input: str,
        system_prompt: str,
        history: list[dict] | None = None,
    ) -> str:
        """Route query — Ollama first, Groq fallback."""
        return await self.groq.query(user_input, system_prompt, history)
