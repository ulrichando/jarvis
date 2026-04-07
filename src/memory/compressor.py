"""Context compressor — reduces conversation history to fit token budgets.

When the accumulated history exceeds `max_tokens`, older turns are
summarised into a single injected context block. Recent turns are always
kept verbatim for short-term coherence.

Works transparently for every backend: Anthropic, Groq, Ollama.
"""

import hashlib
import json
import logging

log = logging.getLogger("jarvis.compressor")

# 1 token ≈ 4 characters (conservative estimate)
_CHARS_PER_TOKEN = 4


def _token_estimate(messages: list[dict]) -> int:
    return sum(len(str(m.get("content", ""))) for m in messages) // _CHARS_PER_TOKEN


class ContextCompressor:
    """Summarise old conversation turns to keep context within token budgets.

    Usage::

        compressor = ContextCompressor(providers=registry, max_tokens=6000)
        history = await compressor.compress(history)

    Parameters
    ----------
    providers:
        A ``ProviderRegistry`` instance.  Used only for the summarisation
        LLM call; the call uses no history so there is no recursion risk.
    max_tokens:
        Token budget.  History is compressed only when this is exceeded.
    keep_recent:
        Number of most-recent turns to keep verbatim (never summarised).
    """

    _SYSTEM = (
        "You are a conversation summariser. "
        "Output only the summary — no preamble, no commentary."
    )

    _PROMPT = (
        "Summarise the following conversation in under 150 words. "
        "Focus on: decisions made, facts stated, ongoing tasks, and user "
        "preferences. Be factual and concise.\n\n{history}"
    )

    def __init__(
        self,
        providers=None,
        max_tokens: int = 6000,
        keep_recent: int = 12,
    ):
        self._providers = providers
        self._max_tokens = max_tokens
        self._keep_recent = keep_recent
        # sha256[:16] → summary string
        self._cache: dict[str, str] = {}

    # ── public ────────────────────────────────────────────────────────

    async def compress(self, history: list[dict]) -> list[dict]:
        """Return (possibly compressed) history.

        If the history is within budget it is returned unchanged.
        Otherwise the oldest turns are replaced with a short summary.
        """
        if not history or _token_estimate(history) <= self._max_tokens:
            return history

        if len(history) <= self._keep_recent:
            return history

        old = history[:-self._keep_recent]
        recent = history[-self._keep_recent:]

        key = self._hash(old)
        if key not in self._cache:
            self._cache[key] = await self._summarise(old)
            log.debug(
                "Compressed %d old turns into %d-char summary",
                len(old), len(self._cache[key]),
            )

        summary = self._cache[key]

        # Inject as a user/assistant pair so every provider handles it cleanly
        return [
            {
                "role": "user",
                "content": f"[Earlier conversation — compressed]\n{summary}",
            },
            {
                "role": "assistant",
                "content": "Understood. Continuing with that context.",
            },
            *recent,
        ]

    # ── private ───────────────────────────────────────────────────────

    def _hash(self, messages: list[dict]) -> str:
        key = json.dumps(
            [
                {
                    "role": m.get("role", ""),
                    "content": str(m.get("content", ""))[:200],
                }
                for m in messages
            ],
            sort_keys=True,
        )
        return hashlib.sha256(key.encode()).hexdigest()[:16]

    async def _summarise(self, messages: list[dict]) -> str:
        history_text = "\n".join(
            f"{m.get('role', 'user').upper()}: {str(m.get('content', ''))[:400]}"
            for m in messages
        )
        prompt = self._PROMPT.format(history=history_text)

        if not self._providers:
            return f"[{len(messages)} earlier messages — context compressed]"

        try:
            result, _ = await self._providers.query(
                user_input=prompt,
                system_prompt=self._SYSTEM,
                history=None,
            )
            return result.strip() or f"[{len(messages)} earlier messages — context compressed]"
        except Exception as exc:
            log.warning("Context summarisation failed: %s", exc)
            # Graceful fallback: list the user-side topics
            topics = [
                str(m.get("content", ""))[:120]
                for m in messages
                if m.get("role") == "user"
            ][:6]
            return "Earlier topics: " + "; ".join(topics)
