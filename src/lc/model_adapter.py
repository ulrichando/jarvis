"""Wraps JARVIS's provider system as a LangChain BaseChatModel.

This lets LangChain components (chains, structured output, etc.) call
through JARVIS's existing multi-provider routing instead of needing
their own API keys configured separately.

Usage:
    from src.lc.model_adapter import JARVISChatModel
    llm = JARVISChatModel()
    response = llm.invoke("What is 2 + 2?")
"""

import logging
import os
from typing import Any, Iterator, List, Optional, Sequence

log = logging.getLogger(__name__)


class JARVISChatModel:
    """Thin LangChain-compatible wrapper around JARVIS providers.

    Implements the minimum interface needed by LangChain chains:
      invoke(input) → AIMessage
      stream(input) → Iterator[AIMessageChunk]

    Uses JARVIS's get_active_providers() → smart routing.
    """

    def __init__(
        self,
        prefer_code: bool = False,
        prefer_smart: bool = False,
        prefer_tool_calling: bool = False,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ):
        self.prefer_code = prefer_code
        self.prefer_smart = prefer_smart
        self.prefer_tool_calling = prefer_tool_calling
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._providers = None

    def _get_providers(self):
        if self._providers is None:
            from src.reasoning.providers import get_active_providers
            self._providers = get_active_providers(
                prefer_code=self.prefer_code,
                prefer_smart=self.prefer_smart,
                prefer_tool_calling=self.prefer_tool_calling,
            )
        return self._providers

    def _messages_to_dicts(self, messages) -> List[dict]:
        """Convert LangChain messages to OpenAI-style dicts."""
        out = []
        for m in messages:
            # Support both LangChain message objects and plain strings
            if hasattr(m, "content") and hasattr(m, "type"):
                role_map = {
                    "human": "user",
                    "ai": "assistant",
                    "system": "system",
                    "tool": "tool",
                }
                role = role_map.get(m.type, "user")
                out.append({"role": role, "content": m.content})
            elif isinstance(m, dict):
                out.append(m)
            elif isinstance(m, str):
                out.append({"role": "user", "content": m})
        return out

    def invoke(self, input_: Any, **kwargs) -> Any:
        """Synchronous call. Returns a dict with 'content' key."""
        import asyncio

        # Normalise input
        if isinstance(input_, str):
            messages = [{"role": "user", "content": input_}]
        elif isinstance(input_, list):
            messages = self._messages_to_dicts(input_)
        else:
            messages = self._messages_to_dicts([input_])

        async def _call():
            providers = self._get_providers()
            for provider in providers:
                try:
                    response = await provider.chat(
                        messages=messages,
                        temperature=self.temperature,
                        max_tokens=self.max_tokens,
                    )
                    return response
                except Exception as e:
                    log.debug("Provider %s failed: %s", provider, e)
                    continue
            return {"content": "Error: all providers failed."}

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                fut = asyncio.run_coroutine_threadsafe(_call(), loop)
                return fut.result(timeout=60)
            return loop.run_until_complete(_call())
        except Exception as e:
            log.error("JARVISChatModel.invoke error: %s", e)
            return {"content": f"Error: {e}"}

    async def ainvoke(self, input_: Any, **kwargs) -> Any:
        """Async call."""
        if isinstance(input_, str):
            messages = [{"role": "user", "content": input_}]
        elif isinstance(input_, list):
            messages = self._messages_to_dicts(input_)
        else:
            messages = self._messages_to_dicts([input_])

        providers = self._get_providers()
        for provider in providers:
            try:
                return await provider.chat(
                    messages=messages,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                )
            except Exception as e:
                log.debug("Provider %s failed: %s", provider, e)
                continue
        return {"content": "Error: all providers failed."}

    def get_num_tokens(self, text: str) -> int:
        """Rough token estimate (4 chars ≈ 1 token)."""
        return len(text) // 4
