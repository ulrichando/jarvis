"""JARVIS OpenAI-Compatible Provider — works with any OpenAI-format API.

Supports: OpenAI, xAI (Grok), Together, OpenRouter, LMStudio, etc.
Features:
- Streaming via SSE
- Tool calling (function calling format)
- Exponential backoff with jitter
- Automatic retry on 429/500/502/503
"""

import os
import json
import time
import random
import logging
from dataclasses import dataclass, field

log = logging.getLogger("jarvis.reasoning.openai")

RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}


@dataclass
class OpenAIConfig:
    """Configuration for an OpenAI-compatible provider."""
    name: str = "openai"
    api_key_env: str = "OPENAI_API_KEY"
    base_url_env: str = "OPENAI_BASE_URL"
    default_base_url: str = "https://api.openai.com/v1"
    default_model: str = "gpt-4o"
    max_retries: int = 2
    initial_backoff: float = 0.5  # seconds
    max_backoff: float = 8.0

    @classmethod
    def openai(cls) -> "OpenAIConfig":
        return cls(name="openai", api_key_env="OPENAI_API_KEY", default_model="gpt-4o")

    @classmethod
    def xai(cls) -> "OpenAIConfig":
        return cls(name="xai", api_key_env="XAI_API_KEY", base_url_env="XAI_BASE_URL",
                   default_base_url="https://api.x.ai/v1", default_model="grok-3")

    @classmethod
    def together(cls) -> "OpenAIConfig":
        return cls(name="together", api_key_env="TOGETHER_API_KEY", base_url_env="TOGETHER_BASE_URL",
                   default_base_url="https://api.together.xyz/v1", default_model="meta-llama/Llama-3.3-70B-Instruct-Turbo")

    @classmethod
    def openrouter(cls) -> "OpenAIConfig":
        return cls(name="openrouter", api_key_env="OPENROUTER_API_KEY", base_url_env="OPENROUTER_BASE_URL",
                   default_base_url="https://openrouter.ai/api/v1", default_model="anthropic/claude-sonnet-4")


class OpenAIClient:
    """Client for OpenAI-compatible APIs with streaming and tool calling."""

    def __init__(self, config: OpenAIConfig = None):
        self.config = config or OpenAIConfig.openai()
        self.api_key = os.environ.get(self.config.api_key_env, "")
        self.base_url = os.environ.get(self.config.base_url_env, self.config.default_base_url).rstrip("/")
        self.model = self.config.default_model

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def set_model(self, model: str):
        self.model = model

    async def query(self, messages: list[dict], model: str = None) -> dict:
        """Send a chat completion request. Returns parsed response."""
        import httpx

        body = {
            "model": model or self.model,
            "messages": messages,
        }

        for attempt in range(self.config.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=120) as client:
                    resp = await client.post(
                        f"{self.base_url}/chat/completions",
                        json=body,
                        headers=self._headers(),
                    )
                    if resp.status_code in RETRYABLE_STATUS and attempt < self.config.max_retries:
                        await self._backoff(attempt)
                        continue
                    resp.raise_for_status()
                    return resp.json()
            except Exception as e:
                if attempt < self.config.max_retries:
                    await self._backoff(attempt)
                    continue
                raise

    async def query_with_tools(self, messages: list[dict], tools: list[dict], model: str = None) -> dict:
        """Chat completion with function calling. Returns dict with text and tool_calls."""
        import httpx

        body = {
            "model": model or self.model,
            "messages": messages,
            "tools": tools,
        }

        for attempt in range(self.config.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=120) as client:
                    resp = await client.post(
                        f"{self.base_url}/chat/completions",
                        json=body,
                        headers=self._headers(),
                    )
                    if resp.status_code in RETRYABLE_STATUS and attempt < self.config.max_retries:
                        await self._backoff(attempt)
                        continue
                    resp.raise_for_status()
                    data = resp.json()

                # Normalize to JARVIS format
                choice = data.get("choices", [{}])[0]
                message = choice.get("message", {})
                text = message.get("content", "") or ""
                tool_calls = []
                for tc in message.get("tool_calls", []):
                    func = tc.get("function", {})
                    try:
                        args = json.loads(func.get("arguments", "{}"))
                    except json.JSONDecodeError:
                        args = {"raw": func.get("arguments", "")}
                    tool_calls.append({
                        "id": tc.get("id", f"call_{time.time_ns()}"),
                        "name": func.get("name", ""),
                        "args": args,
                    })
                return {"text": text, "tool_calls": tool_calls}
            except Exception as e:
                if attempt < self.config.max_retries:
                    await self._backoff(attempt)
                    continue
                raise

    async def query_stream(self, messages: list[dict], model: str = None):
        """Streaming chat completion. Yields text chunks."""
        import httpx
        from brain.sse import SseParser

        body = {
            "model": model or self.model,
            "messages": messages,
            "stream": True,
        }

        parser = SseParser()
        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream("POST", f"{self.base_url}/chat/completions",
                                     json=body, headers=self._headers()) as resp:
                async for chunk in resp.aiter_bytes():
                    events = parser.push(chunk)
                    for event in events:
                        if event.parsed:
                            choice = event.parsed.get("choices", [{}])[0]
                            delta = choice.get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                yield content

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def _backoff(self, attempt: int):
        """Exponential backoff with jitter."""
        import asyncio
        delay = min(self.config.max_backoff, self.config.initial_backoff * (2 ** attempt))
        jitter = delay * random.uniform(0.5, 1.0)
        log.warning("Retrying in %.1fs (attempt %d)", jitter, attempt + 1)
        await asyncio.sleep(jitter)
