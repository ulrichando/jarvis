"""JARVIS AI Provider Registry — plug in any AI backend.

Supports:
- Claude (Anthropic API / OAuth tokens)
- OpenAI-compatible APIs (OpenAI, Together, OpenRouter, local Ollama)
- Any endpoint that speaks the OpenAI chat completions format

Providers are stored in the vault and hot-loaded at runtime.
Users can add new providers through the web UI.
"""

import asyncio
import json
import os
from dataclasses import dataclass
from src.config import JARVIS_HOME

PROVIDERS_FILE = JARVIS_HOME / "providers.json"

# Known provider templates — auto-detect from API key prefix or name
TEMPLATES = {
    "claude": {
        "type": "anthropic",
        "base_url": "https://api.anthropic.com",
        "models": ["claude-haiku-4-5-20251001", "claude-sonnet-4-20250514"],
        "default_model": "claude-haiku-4-5-20251001",
    },
    "openai": {
        "type": "openai",
        "base_url": "https://api.openai.com/v1",
        "models": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo"],
        "default_model": "gpt-4o-mini",
    },
    "together": {
        "type": "openai",
        "base_url": "https://api.together.xyz/v1",
        "models": ["meta-llama/Llama-3-70b-chat-hf"],
        "default_model": "meta-llama/Llama-3-70b-chat-hf",
    },
    "xai": {
        "type": "openai",
        "base_url": "https://api.x.ai/v1",
        "models": ["grok-3", "grok-3-mini"],
        "default_model": "grok-3-mini",
    },
    "openrouter": {
        "type": "openai",
        "base_url": "https://openrouter.ai/api/v1",
        "models": ["anthropic/claude-3.5-sonnet", "openai/gpt-4o"],
        "default_model": "anthropic/claude-3.5-sonnet",
    },
    "deepseek": {
        "type": "openai",
        "base_url": "https://api.deepseek.com",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "default_model": "deepseek-chat",
    },
    "kimi": {
        "type": "openai",
        "base_url": "https://api.moonshot.cn/v1",
        "models": ["kimi-k2-0711", "moonshot-v1-128k"],
        "default_model": "kimi-k2-0711",
    },
    "ollama": {
        "type": "openai",
        "base_url": "http://localhost:11434/v1",
        "models": [
            "llama3.2:3b",
            "deepseek-coder-v2:16b",
            "gemma3:4b",
            "qwen2.5-coder:3b",
            "lazarevtill/Llama-3-WhiteRabbitNeo-8B-v2.0",
            "nomic-embed-text",
        ],
        "default_model": "llama3.2:3b",
        "api_key": "ollama",  # Ollama doesn't need a real key
    },
}


@dataclass
class Provider:
    """A configured AI provider."""
    name: str           # User-friendly name (e.g. "claude", "openai", "my-local")
    type: str           # "anthropic" or "openai" (protocol)
    api_key: str        # API key or token
    base_url: str       # API endpoint
    model: str          # Default model to use
    models: list        # Available models
    priority: int       # Lower = tried first (0 = highest)
    enabled: bool = True

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "type": self.type,
            "api_key": self.api_key,
            "base_url": self.base_url,
            "model": self.model,
            "models": self.models,
            "priority": self.priority,
            "enabled": self.enabled,
        }


class ProviderRegistry:
    """Manages AI providers with circuit breaker for resilience."""

    # Circuit breaker: skip provider after N failures in WINDOW seconds
    _CB_MAX_FAILURES = 3
    _CB_WINDOW = 300       # 5 minutes
    _CB_COOLDOWN = 120     # 2 minutes before retry

    def __init__(self):
        self._providers: dict[str, Provider] = {}
        self._clients: dict[str, object] = {}
        self._last_working: str | None = None
        self._circuit_breaker: dict[str, dict] = {}  # {name: {failures: int, last_fail: float, open_until: float}}
        self._load()
        self._load_env_providers()
        self._load_claude_credentials()

    def _cb_is_open(self, name: str) -> bool:
        """Check if circuit breaker is open (provider should be skipped)."""
        import time
        cb = self._circuit_breaker.get(name)
        if not cb:
            return False
        if time.time() < cb.get("open_until", 0):
            return True  # Still in cooldown
        # Cooldown expired — half-open, allow one attempt
        return False

    def _cb_record_failure(self, name: str):
        """Record a provider failure for circuit breaker."""
        import time
        now = time.time()
        cb = self._circuit_breaker.setdefault(name, {"failures": 0, "last_fail": 0, "open_until": 0})
        # Reset if last failure was outside the window
        if now - cb["last_fail"] > self._CB_WINDOW:
            cb["failures"] = 0
        cb["failures"] += 1
        cb["last_fail"] = now
        if cb["failures"] >= self._CB_MAX_FAILURES:
            cb["open_until"] = now + self._CB_COOLDOWN
            print(f"[JARVIS] Circuit breaker OPEN for {name} — skipping for {self._CB_COOLDOWN}s")

    def _cb_record_success(self, name: str):
        """Record a success — close the circuit breaker."""
        if name in self._circuit_breaker:
            self._circuit_breaker[name] = {"failures": 0, "last_fail": 0, "open_until": 0}

    # ── Public API ──────────────────────────────────────────────────

    def add_provider(self, name: str, api_key: str, base_url: str = "",
                     model: str = "", provider_type: str = "") -> Provider:
        """Add or update a provider. Auto-detects type from key/name."""
        name = name.lower().strip()

        # Auto-detect from templates or key prefix
        template = TEMPLATES.get(name, {})
        if not provider_type:
            provider_type = template.get("type", "") or self._detect_type(api_key)
        if not base_url:
            base_url = template.get("base_url", "")
        models = template.get("models", [])
        if not model:
            model = template.get("default_model", "")

        priority = len(self._providers)

        provider = Provider(
            name=name,
            type=provider_type,
            api_key=api_key,
            base_url=base_url,
            model=model,
            models=models,
            priority=priority,
        )
        self._providers[name] = provider
        self._clients.pop(name, None)  # Clear cached client
        self._save()
        return provider

    def remove_provider(self, name: str):
        """Remove a provider."""
        name = name.lower().strip()
        self._providers.pop(name, None)
        self._clients.pop(name, None)
        self._save()

    def list_providers(self) -> list[dict]:
        """List all providers (keys masked)."""
        result = []
        for p in sorted(self._providers.values(), key=lambda x: x.priority):
            d = p.to_dict()
            # Mask API key for display
            key = d["api_key"]
            if len(key) > 12:
                d["api_key_masked"] = key[:8] + "..." + key[-4:]
            else:
                d["api_key_masked"] = "***"
            del d["api_key"]
            result.append(d)
        return result

    def get_active_providers(self, prefer_code: bool = False,
                             prefer_tool_calling: bool = False,
                             prefer_smart: bool = False) -> list[Provider]:
        """Get enabled providers sorted by priority with smart reordering.

        prefer_tool_calling: Cloud providers first (fast, native function calling)
        prefer_smart: 70B+ models first (complex reasoning)
        prefer_code: Code-specialized models first
        Multiple flags can combine — tool_calling takes highest precedence.
        """
        providers = sorted(
            [p for p in self._providers.values() if p.enabled],
            key=lambda x: x.priority,
        )

        def _is_cloud(p):
            return "localhost" not in p.base_url and "127.0.0.1" not in p.base_url

        def _is_smart(p):
            return any(big in p.model for big in ["70b", "72b", "65b", "mixtral", "32b"])

        def _is_code(p):
            code_names = {"ollama-code", "deepseek", "codestral"}
            code_models = {"deepseek", "codestral", "starcoder", "codegemma", "qwen2.5:72b"}
            return p.name in code_names or any(cm in p.model.lower() for cm in code_models)

        if prefer_tool_calling:
            # Cloud smart > Cloud fast > Local smart > Local small
            cloud_smart = [p for p in providers if _is_cloud(p) and _is_smart(p)]
            cloud_other = [p for p in providers if _is_cloud(p) and not _is_smart(p)]
            local_smart = [p for p in providers if not _is_cloud(p) and _is_smart(p)]
            local_other = [p for p in providers if not _is_cloud(p) and not _is_smart(p)]
            return cloud_smart + cloud_other + local_smart + local_other

        if prefer_smart:
            smart = [p for p in providers if _is_smart(p)]
            other = [p for p in providers if not _is_smart(p)]
            return smart + other

        if prefer_code:
            code = [p for p in providers if _is_code(p)]
            other = [p for p in providers if not _is_code(p)]
            return code + other

        return providers

    # ── Query Methods ───────────────────────────────────────────────

    async def query(self, user_input: str, system_prompt: str,
                    history: list[dict] | None = None) -> tuple[str, str]:
        """Query providers in priority order. Returns (response, provider_name)."""
        errors = []
        for provider in self.get_active_providers():
            try:
                result = await self._query_provider(provider, user_input, system_prompt, history)
                if result:
                    return result, f"{provider.name}:{provider.model}"
                errors.append(f"{provider.name}: empty response")
            except Exception as e:
                errors.append(f"{provider.name}: {e}")
                continue
        if errors:
            import logging
            logging.getLogger("jarvis.providers").debug("All providers failed: %s", "; ".join(errors))
        return "", "none"

    async def query_stream(self, user_input: str, system_prompt: str,
                           history: list[dict] | None = None):
        """Streaming query — yields text chunks. Falls back to non-streaming."""
        for provider in self.get_active_providers():
            try:
                if provider.type == "anthropic":
                    had_output = False
                    async for chunk in self._stream_anthropic(provider, user_input, system_prompt, history):
                        had_output = True
                        yield chunk
                    if had_output:
                        return
                else:
                    # OpenAI-compatible: fall back to non-streaming
                    result = await self._query_openai(provider, user_input, system_prompt, history)
                    if result:
                        yield result
                        return
            except Exception:
                continue

    async def query_with_tools(self, messages: list[dict], tools: list[dict],
                               system: str = "") -> tuple[dict, str]:
        """Tool-calling query across providers. Falls back to plain query if tool calling fails.

        Priority for tool calling:
        1. Cloud providers (Anthropic, OpenAI) — fast, native tool calling support
        2. Large local models (70B+) — slower but capable
        3. Small local models — only as last resort (prompt-based tools)
        """
        # Extract system prompt from messages if not passed explicitly
        if not system:
            for m in messages:
                if m.get("role") == "system":
                    system = m.get("content", "")
                    break

        is_code = False
        for m in messages:
            content = (m.get("content") or "").lower()
            if any(kw in content for kw in ["review", "debug", "fix", "refactor",
                                             "function", "class ", ".py", ".rs", ".js",
                                             "codebase", "code", "source", "bug",
                                             "create", "build", "extension", "app"]):
                is_code = True
                break

        errors = []
        for provider in self.get_active_providers(prefer_tool_calling=True, prefer_code=is_code):
            # Circuit breaker — skip providers that have failed repeatedly
            if self._cb_is_open(provider.name):
                errors.append(f"{provider.name}: circuit breaker open")
                continue
            try:
                result = await self._query_tools_provider(provider, messages, tools, system)
                tc = result.get("tool_calls", [])
                if tc or (result.get("text") and len(result["text"]) > 5):
                    self._last_working = provider.name
                    self._cb_record_success(provider.name)
                    return result, f"{provider.name}:{provider.model}"
                errors.append(f"{provider.name}: no tool result")
                self._cb_record_failure(provider.name)
            except Exception as e:
                errors.append(f"{provider.name}: {e}")
                self._cb_record_failure(provider.name)
                continue

        # Fallback: try plain query without tools (so LLM at least responds)
        for provider in self.get_active_providers():
            try:
                # Extract last user message from messages list
                user_msg = ""
                for m in reversed(messages):
                    if m.get("role") == "user":
                        user_msg = m.get("content", "")
                        break
                if user_msg:
                    result = await self._query_provider(provider, user_msg, system, None)
                    if result and len(result) > 3:
                        return {"text": result, "tool_calls": []}, f"{provider.name}:{provider.model}"
            except Exception:
                continue

        # Broadcast provider failure so frontend can show setup wizard
        try:
            from src.server import _provider_error
            _provider_error["failed"] = True
            _provider_error["errors"] = errors[:3]
        except ImportError:
            pass

        # All providers failed — help the user fix it
        has_ollama = any(
            "localhost" in p.base_url or "127.0.0.1" in p.base_url
            for p in self.providers if p.enabled
        )
        has_cloud = any(
            "localhost" not in p.base_url and "127.0.0.1" not in p.base_url
            for p in self.providers if p.enabled
        )

        lines = ["I can't reach any AI provider right now."]
        if errors:
            lines.append(f"Errors: {'; '.join(errors[:3])}")
        lines.append("")
        lines.append("To fix this:")
        if not has_ollama:
            lines.append("• Run a local model: ollama pull llama3.3 && ollama serve")
            lines.append("  Then add Ollama in /providers or run /doctor")
        else:
            lines.append("• Start Ollama: ollama serve (local models, no internet needed)")
        if not has_cloud:
            lines.append("• Add a cloud API key: /provider add groq <key> (free at console.groq.com)")
            lines.append("• Or: /provider add anthropic <key> (console.anthropic.com)")
        else:
            lines.append("• Check your API keys — they may be expired or out of credits")
            lines.append("• Groq is free: sign up at console.groq.com")
        lines.append("")
        lines.append("Run /doctor for a full diagnostic.")

        return {"text": "\n".join(lines), "tool_calls": []}, "none"

    # ── Provider-Specific Query ─────────────────────────────────────

    async def _query_provider(self, provider: Provider, user_input: str,
                              system_prompt: str, history: list[dict] | None) -> str:
        if provider.type == "anthropic":
            return await self._query_anthropic(provider, user_input, system_prompt, history)
        else:
            return await self._query_openai(provider, user_input, system_prompt, history)

    async def query_vision(self, image_b64: str, prompt: str,
                           system_prompt: str = "You are JARVIS, an AI assistant with vision. Describe what you see concisely.") -> tuple[str, str]:
        """Send an image to a vision-capable provider. Returns (response, provider_name)."""
        for provider in self.get_active_providers():
            if provider.type == "anthropic":
                result = await self._query_anthropic_vision(provider, image_b64, prompt, system_prompt)
            else:
                result = await self._query_openai_vision(provider, image_b64, prompt, system_prompt)
            if result:
                return result, provider.name
        return "", "none"

    async def _query_anthropic_vision(self, provider: Provider, image_b64: str,
                                       prompt: str, system_prompt: str) -> str:
        client = self._get_anthropic_client(provider)
        if not client:
            return ""
        messages = [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}},
            {"type": "text", "text": prompt},
        ]}]
        def _call():
            for model in [provider.model] + [m for m in (provider.models or []) if m != provider.model]:
                try:
                    r = client.messages.create(
                        model=model, max_tokens=1024,
                        system=system_prompt, messages=messages,
                    )
                    for block in r.content:
                        if block.type == "text":
                            return block.text
                    return ""
                except Exception:
                    continue
            return ""
        return await asyncio.to_thread(_call)

    async def _query_openai_vision(self, provider: Provider, image_b64: str,
                                    prompt: str, system_prompt: str) -> str:
        client = self._get_openai_client(provider)
        if not client:
            return ""
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                {"type": "text", "text": prompt},
            ]},
        ]
        def _call():
            try:
                chat = client.chat.completions.create(
                    messages=messages, model=provider.model,
                    temperature=0.3, max_completion_tokens=4096,
                )
                return chat.choices[0].message.content or ""
            except Exception:
                return ""
        return await asyncio.to_thread(_call)

    async def _query_anthropic(self, provider: Provider, user_input: str,
                               system_prompt: str, history: list[dict] | None) -> str:
        client = self._get_anthropic_client(provider)
        if not client:
            return ""

        messages = self._build_anthropic_messages(history, user_input)

        # Cached system prompt
        system_blocks = [{
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        }] if system_prompt else None

        def _call():
            for model in [provider.model] + [m for m in (provider.models or []) if m != provider.model]:
                try:
                    r = client.messages.create(
                        model=model, max_tokens=8192,
                        system=system_blocks,
                        messages=messages,
                    )
                    provider.model = model
                    for block in r.content:
                        if block.type == "text":
                            return block.text
                    return ""
                except Exception:
                    continue
            return ""

        return await asyncio.to_thread(_call)

    async def _stream_anthropic(self, provider: Provider, user_input: str,
                                system_prompt: str, history: list[dict] | None):
        """Streaming query for Anthropic — yields text chunks as they arrive."""
        client = self._get_anthropic_client(provider)
        if not client:
            return

        messages = self._build_anthropic_messages(history, user_input)

        # Cached system prompt
        system_blocks = [{
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        }] if system_prompt else None

        def _stream():
            for model in [provider.model] + [m for m in (provider.models or []) if m != provider.model]:
                try:
                    with client.messages.stream(
                        model=model, max_tokens=8192,
                        system=system_blocks, messages=messages,
                    ) as stream:
                        provider.model = model
                        for text in stream.text_stream:
                            yield text
                    return
                except Exception:
                    continue

        # Run the blocking generator in a thread, pushing chunks through a queue
        import queue
        q = queue.Queue()
        sentinel = object()

        def _run():
            try:
                for chunk in _stream():
                    q.put(chunk)
            except Exception as e:
                q.put(e)
            finally:
                q.put(sentinel)

        loop = asyncio.get_event_loop()
        loop.run_in_executor(None, _run)

        while True:
            item = await asyncio.to_thread(q.get)
            if item is sentinel:
                break
            if isinstance(item, Exception):
                break
            yield item

    def _build_anthropic_messages(self, history, user_input):
        messages = []
        if history:
            for turn in history[-6:]:
                role = "assistant" if turn["role"] == "jarvis" else "user"
                messages.append({"role": role, "content": turn["content"][:500]})
        messages.append({"role": "user", "content": user_input})
        return messages

    async def _query_openai(self, provider: Provider, user_input: str,
                            system_prompt: str, history: list[dict] | None) -> str:
        client = self._get_openai_client(provider)
        if not client:
            return ""

        messages = [{"role": "system", "content": system_prompt}]
        if history:
            for turn in history[-6:]:
                role = "assistant" if turn["role"] == "jarvis" else "user"
                messages.append({"role": role, "content": turn["content"][:500]})
        messages.append({"role": "user", "content": user_input})

        def _call():
            chat = client.chat.completions.create(
                messages=messages,
                model=provider.model,
                temperature=0.4,
                max_completion_tokens=4096,
            )
            return chat.choices[0].message.content or ""

        return await asyncio.to_thread(_call)

    async def _query_tools_provider(self, provider: Provider, messages: list[dict],
                                    tools: list[dict], system: str) -> dict:
        if provider.type == "anthropic":
            return await self._query_tools_anthropic(provider, messages, tools, system)
        else:
            return await self._query_tools_openai(provider, messages, tools, system)

    async def _query_tools_anthropic(self, provider: Provider, messages: list[dict],
                                     tools: list[dict], system: str) -> dict:
        client = self._get_anthropic_client(provider)
        if not client:
            return {"text": "", "tool_calls": []}

        claude_tools = []
        for t in tools:
            func = t.get("function", {})
            claude_tools.append({
                "name": func.get("name", ""),
                "description": func.get("description", ""),
                "input_schema": func.get("parameters", {}),
            })

        claude_messages = self._convert_messages_for_anthropic(messages, system)

        # System prompt as array with caching (90% cost savings on repeated calls)
        system_blocks = []
        if system:
            system_blocks.append({
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},  # 5-min cache TTL
            })
            # Brief identity reminder (uncached)
            system_blocks.append({
                "type": "text",
                "text": "Remember: you are JARVIS, not Claude. Built by Ulrich.",
            })

        # Cache tool definitions too (they don't change between calls)
        if claude_tools:
            claude_tools[-1]["cache_control"] = {"type": "ephemeral"}

        def _call():
            for model in [provider.model] + [m for m in (provider.models or []) if m != provider.model]:
                try:
                    # Build kwargs — system must be a list (not None) for Anthropic
                    kwargs = {
                        "model": model,
                        "max_tokens": 8192,
                        "messages": claude_messages,
                    }
                    if system_blocks:
                        kwargs["system"] = system_blocks
                    if claude_tools:
                        kwargs["tools"] = claude_tools

                    # Extended thinking — adaptive budget based on query complexity
                    _use_thinking = any(m in model for m in ["opus-4", "sonnet-4", "haiku-4"])
                    if _use_thinking:
                        # Check if query is simple (short casual chat) vs complex (code/tools)
                        _last_user = ""
                        for _m in reversed(claude_messages):
                            if _m.get("role") == "user":
                                _last_user = str(_m.get("content", "")).lower()
                                break
                        _has_tools = bool(claude_tools)
                        _is_complex = _has_tools and any(
                            w in _last_user for w in [
                                "fix", "create", "build", "install", "review", "debug",
                                "write", "edit", "find", "search", "scan", "deploy",
                                "explain", "analyze", "investigate", "set up",
                            ]
                        )
                        _budget = 8000 if _is_complex else 1024
                        kwargs["thinking"] = {"type": "enabled", "budget_tokens": _budget}
                        kwargs["max_tokens"] = _budget + 8192

                    # Use streaming for thinking models to avoid SDK timeout
                    if _use_thinking:
                        text, tool_calls, thinking = "", [], ""
                        usage = {}
                        with client.messages.stream(**kwargs) as stream:
                            r = stream.get_final_message()
                        for block in r.content:
                            if block.type == "text":
                                text += block.text
                            elif block.type == "tool_use":
                                tool_calls.append({"id": block.id, "name": block.name, "args": block.input})
                            elif block.type == "thinking":
                                thinking += block.thinking
                        # Check rate limit headers
                        try:
                            headers = getattr(r, '_headers', {}) or {}
                            remaining = headers.get('x-ratelimit-remaining-tokens') or headers.get('x-ratelimit-remaining')
                            if remaining and int(remaining) < 100:
                                import logging
                                logging.getLogger("jarvis.providers").warning(
                                    "Rate limit low on %s: %s remaining", model, remaining)
                        except Exception:
                            pass

                        if hasattr(r, 'usage') and r.usage:
                            usage = {
                                "input": r.usage.input_tokens,
                                "output": r.usage.output_tokens,
                                "cache_read": getattr(r.usage, 'cache_read_input_tokens', 0),
                                "cache_creation": getattr(r.usage, 'cache_creation_input_tokens', 0),
                            }
                        provider.model = model
                        return {"text": text, "tool_calls": tool_calls, "usage": usage, "thinking": thinking}

                    r = client.messages.create(**kwargs)
                    provider.model = model

                    # Debug: log tool call status
                    import logging as _log_mod
                    _dbg = _log_mod.getLogger("jarvis.tools")
                    _dbg.info("Anthropic response: stop=%s, tools_passed=%d, content_blocks=%s",
                              getattr(r, 'stop_reason', '?'), len(claude_tools),
                              [b.type for b in r.content])
                    if not any(b.type == "tool_use" for b in r.content) and claude_tools:
                        _dbg.warning("Model did NOT use tools despite %d tools available", len(claude_tools))

                    text, tool_calls, thinking = "", [], ""
                    for block in r.content:
                        if block.type == "text":
                            text += block.text
                        elif block.type == "tool_use":
                            tool_calls.append({"id": block.id, "name": block.name, "args": block.input})
                        elif block.type == "thinking":
                            thinking += block.thinking

                    usage = {}
                    if hasattr(r, 'usage') and r.usage:
                        usage = {
                            "input": r.usage.input_tokens,
                            "output": r.usage.output_tokens,
                            "cache_read": getattr(r.usage, 'cache_read_input_tokens', 0),
                            "cache_creation": getattr(r.usage, 'cache_creation_input_tokens', 0),
                        }
                    return {"text": text, "tool_calls": tool_calls, "usage": usage, "thinking": thinking}
                except Exception as e:
                    import logging
                    logging.getLogger("jarvis.providers").debug("Anthropic tools error (%s): %s", model, e)
                    continue
            return {"text": "", "tool_calls": []}

        return await asyncio.to_thread(_call)

    def _convert_messages_for_anthropic(self, messages: list[dict], system: str) -> list[dict]:
        """Convert OpenAI-format messages (with tool_calls/tool roles) to Anthropic format.

        OpenAI: assistant has tool_calls array, followed by role=tool messages
        Anthropic: assistant has content blocks with tool_use, followed by user with tool_result blocks
        """
        result = []
        i = 0
        while i < len(messages):
            msg = messages[i]
            role = msg.get("role", "")

            # Skip system messages (handled separately)
            if role == "system":
                i += 1
                continue

            # Regular user/assistant messages
            if role == "user":
                content = msg.get("content", "")
                if isinstance(content, str):
                    result.append({"role": "user", "content": content})
                else:
                    result.append({"role": "user", "content": content})
                i += 1
                continue

            if role == "assistant":
                tool_calls = msg.get("tool_calls", [])
                if not tool_calls:
                    # Plain assistant message
                    content = msg.get("content") or ""
                    if content:
                        result.append({"role": "assistant", "content": content})
                    i += 1
                    continue

                # Assistant with tool calls → Anthropic tool_use blocks
                content_blocks = []
                text = msg.get("content") or ""
                if text:
                    content_blocks.append({"type": "text", "text": text})

                for tc in tool_calls:
                    func = tc.get("function", {})
                    args = func.get("arguments", "{}")
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except Exception:
                            args = {"raw": args}
                    content_blocks.append({
                        "type": "tool_use",
                        "id": tc.get("id", "tool_0"),
                        "name": func.get("name", "unknown"),
                        "input": args,
                    })

                result.append({"role": "assistant", "content": content_blocks})
                i += 1

                # Collect subsequent tool result messages
                tool_results = []
                while i < len(messages) and messages[i].get("role") == "tool":
                    tool_msg = messages[i]
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_msg.get("tool_call_id", "tool_0"),
                        "content": tool_msg.get("content", ""),
                    })
                    i += 1

                if tool_results:
                    result.append({"role": "user", "content": tool_results})
                continue

            # Tool messages without preceding assistant (shouldn't happen, but handle gracefully)
            if role == "tool":
                i += 1
                continue

            i += 1

        # Ensure messages alternate user/assistant (Anthropic requirement)
        if result and result[0].get("role") == "assistant":
            result.insert(0, {"role": "user", "content": "Continue."})

        # Cache the second-to-last message (conversation history prefix)
        # This means only the latest turn is uncached — huge savings on multi-turn
        if len(result) >= 3:
            cache_target = result[-2]  # Last message before current user turn
            content = cache_target.get("content")
            if isinstance(content, str):
                cache_target["content"] = [{
                    "type": "text", "text": content,
                    "cache_control": {"type": "ephemeral"},
                }]
            elif isinstance(content, list) and content:
                # Add cache_control to last block
                last_block = content[-1]
                if isinstance(last_block, dict):
                    last_block["cache_control"] = {"type": "ephemeral"}

        return result

    async def _query_tools_openai(self, provider: Provider, messages: list[dict],
                                  tools: list[dict], system: str) -> dict:
        client = self._get_openai_client(provider)
        if not client:
            return {"text": "", "tool_calls": []}

        is_local = "localhost" in provider.base_url or "127.0.0.1" in provider.base_url

        def _call():
            # Prompt-based tool calling is ONLY for tiny models that truly can't do native
            # Modern Ollama (0.1.33+) supports native tool calling — use it
            if is_local and not tools:
                return self._prompt_based_tool_call(client, provider, messages, tools)

            # Cloud API: native function calling with retry on rate limit
            import time as _time
            last_error = None
            for attempt in range(3):
                try:
                    # Try each model in provider's model list
                    for model in (provider.models or [provider.model]):
                        try:
                            kwargs = {
                                "messages": messages,
                                "model": model,
                                "tools": tools,
                                "temperature": 0.3,
                                "max_tokens": 4096,
                            }
                            # tool_choice: Ollama doesn't always support it
                            if not is_local:
                                kwargs["tool_choice"] = "auto"
                            chat = client.chat.completions.create(**kwargs)
                            msg = chat.choices[0].message
                            result = {"text": msg.content or "", "tool_calls": [], "usage": {}}
                            if hasattr(chat, 'usage') and chat.usage:
                                result["usage"] = {
                                    "input": getattr(chat.usage, 'prompt_tokens', 0),
                                    "output": getattr(chat.usage, 'completion_tokens', 0),
                                }
                            if msg.tool_calls:
                                for tc in msg.tool_calls:
                                    try:
                                        args = json.loads(tc.function.arguments)
                                    except json.JSONDecodeError:
                                        # Lenient JSON: try fixing common LLM mistakes
                                        raw = tc.function.arguments
                                        try:
                                            # Fix trailing commas, single quotes
                                            fixed = raw.replace("'", '"').rstrip(",}")  + "}"
                                            args = json.loads(fixed)
                                        except Exception:
                                            # Last resort: extract key-value from malformed string
                                            import re as _re_json
                                            m = _re_json.search(r'"(\w+)":\s*"([^"]*)"', raw)
                                            args = {m.group(1): m.group(2)} if m else {"command": raw}
                                    result["tool_calls"].append({
                                        "id": tc.id, "name": tc.function.name, "args": args,
                                    })
                            # Fallback: some models put tool calls in text as JSON
                            if not result["tool_calls"] and result["text"]:
                                import re as _re_tc
                                # Match {"name": "...", "parameters": {...}} or {"command": "..."}
                                for tool in tools:
                                    fname = tool["function"]["name"]
                                    props = list(tool["function"]["parameters"].get("properties", {}).keys())
                                    if props:
                                        # Try to extract the first property value from JSON in text
                                        m = _re_tc.search(r'\{[^}]*"' + props[0] + r'":\s*"([^"]+)"', result["text"])
                                        if m:
                                            result["tool_calls"].append({
                                                "id": "tc_parsed", "name": fname,
                                                "args": {props[0]: m.group(1)},
                                            })
                                            result["text"] = ""
                                            break
                            return result
                        except Exception as e:
                            err_str = str(e).lower()
                            # Tool call format failed — retry with parsed tool call from error
                            if "tool_use_failed" in err_str and "failed_generation" in err_str:
                                try:
                                    import re as _re_tool
                                    gen = str(e)
                                    m = _re_tool.search(r'<function=(\w+)>(\{[^}]+\})', gen)
                                    if m:
                                        return {
                                            "text": "",
                                            "tool_calls": [{"id": "tc_0", "name": m.group(1), "args": json.loads(m.group(2))}],
                                            "usage": {},
                                        }
                                except Exception:
                                    pass
                            if "rate_limit" in err_str or "429" in err_str or "quota" in err_str:
                                last_error = e
                                # Rate limited on this model, try next model in list
                                continue
                            raise  # Not a rate limit error, bubble up
                except Exception as e:
                    last_error = e
                    err_str = str(e).lower()
                    if "rate_limit" in err_str or "429" in err_str:
                        wait = min(5 * (attempt + 1), 15)
                        _time.sleep(wait)
                        continue
                    raise
            # All retries exhausted
            if last_error:
                raise last_error
            return {"text": "", "tool_calls": []}

        return await asyncio.to_thread(_call)

    def _prompt_based_tool_call(self, client, provider, messages, tools) -> dict:
        """Prompt-based tool calling for local models that don't support function calling.

        Uses native Ollama API (/api/chat) with tool instructions in the prompt.
        Parses ACTION: tool_name({"arg": "value"}) from the response.
        """
        import re as _re
        import urllib.request

        # Build tool description with examples
        tool_param_map = {}  # name -> first param key
        tool_lines = []
        for t in tools:
            func = t.get("function", {})
            name = func.get("name", "")
            desc = func.get("description", "")
            params = func.get("parameters", {}).get("properties", {})
            first_param = list(params.keys())[0] if params else "input"
            tool_param_map[name] = first_param
            tool_lines.append(f"- {name}: {desc}. Parameter: {first_param}")

        tool_desc = f"""TOOLS: You can call tools by writing exactly this format (no markdown, no json blocks):

CALL: tool_name {{"param": "value"}}

Available tools:
{chr(10).join(tool_lines)}

EXAMPLES:
CALL: bash {{"command": "ls -la"}}
CALL: read_file {{"path": "/etc/hosts"}}
CALL: search_files {{"pattern": "*.py"}}

RULES:
- Write CALL: on its own line
- Use the exact parameter names shown above
- No markdown, no ```json blocks, no explanation before the CALL
- You can write text AFTER the CALL line
- If no tool needed, just respond normally"""

        # Build messages with tool instructions
        enhanced = []
        for m in messages:
            if m.get("role") == "system":
                enhanced.append({"role": "system", "content": m["content"] + "\n\n" + tool_desc})
            else:
                enhanced.append(m)
        if not any(m.get("role") == "system" for m in enhanced):
            enhanced.insert(0, {"role": "system", "content": tool_desc})

        # Use native Ollama API (more reliable than OpenAI compat for local models)
        base = provider.base_url.replace("/v1", "").rstrip("/")
        data = json.dumps({
            "model": provider.model,
            "messages": enhanced,
            "stream": False,
        }).encode()

        try:
            req = urllib.request.Request(
                f"{base}/api/chat", data=data,
                headers={"Content-Type": "application/json"},
            )
            resp = urllib.request.urlopen(req, timeout=120)
            result = json.loads(resp.read())
            text = result.get("message", {}).get("content", "")
        except Exception as e:
            # Fallback to OpenAI client
            try:
                chat = client.chat.completions.create(
                    messages=enhanced, model=provider.model,
                    temperature=0.2, max_completion_tokens=4096,
                )
                text = chat.choices[0].message.content or ""
            except Exception:
                return {"text": "", "tool_calls": []}

        # Parse tool calls from response — multiple formats
        tool_calls = []

        def _add_tool_call(name: str, args_str: str):
            try:
                raw_args = json.loads(args_str)
            except (json.JSONDecodeError, TypeError):
                raw_args = {"command": args_str} if name == "bash" else {"raw": args_str}
            # Normalize arg names
            if name in tool_param_map:
                expected_key = tool_param_map[name]
                if expected_key not in raw_args and len(raw_args) == 1:
                    actual_key = list(raw_args.keys())[0]
                    raw_args = {expected_key: raw_args[actual_key]}
            tool_calls.append({
                "id": f"local_{len(tool_calls)}",
                "name": name,
                "args": raw_args,
            })

        # Format 1: CALL: tool_name {"param": "value"}
        call_pattern = _re.compile(r'CALL:\s*(\w+)\s+(\{[^}]+\})', _re.DOTALL)
        # Format 2: ACTION: tool_name({"param": "value"})
        action_pattern = _re.compile(r'ACTION:\s*(\w+)\((\{[^}]+\})\)', _re.DOTALL)

        for pattern in [call_pattern, action_pattern]:
            for match in pattern.finditer(text):
                _add_tool_call(match.group(1), match.group(2))

        # Format 3: XML-style <tool_use><tool_name>bash</tool_name><tool_parameter name="command">...</tool_parameter></tool_use>
        xml_tool_pat = _re.compile(
            r'<tool_use>\s*<tool_name>(\w+)</tool_name>\s*'
            r'<tool_parameter\s+name="(\w+)">(.*?)</tool_parameter>\s*</tool_use>',
            _re.DOTALL
        )
        for m in xml_tool_pat.finditer(text):
            _add_tool_call(m.group(1), json.dumps({m.group(2): m.group(3).strip()}))

        # Format 4: <function_calls><invoke name="tool"><parameter name="k">v</parameter></invoke></function_calls>
        invoke_pat = _re.compile(
            r'<invoke\s+name="(\w+)">(.*?)</invoke>',
            _re.DOTALL
        )
        param_pat = _re.compile(r'<parameter\s+name="(\w+)">(.*?)</parameter>', _re.DOTALL)
        for m in invoke_pat.finditer(text):
            params = {pm.group(1): pm.group(2).strip() for pm in param_pat.finditer(m.group(2))}
            _add_tool_call(m.group(1), json.dumps(params))

        # Format 5: <bash>command</bash> or <tool_name>{"args"}</tool_name>
        simple_xml_pat = _re.compile(r'<(bash|read_file|write_file|edit_file|search_files|web_search|web_fetch)>(.*?)</\1>', _re.DOTALL)
        for m in simple_xml_pat.finditer(text):
            name = m.group(1)
            body = m.group(2).strip()
            try:
                args = json.loads(body)
                _add_tool_call(name, json.dumps(args))
            except (json.JSONDecodeError, TypeError):
                _add_tool_call(name, json.dumps({"command": body} if name == "bash" else {"query": body}))

        # Format 6: ```bash\ncommand\n``` (markdown code block intended as tool call)
        # Only match if no tool_calls were found yet and the block looks like a command
        if not tool_calls:
            bash_block_pat = _re.compile(r'```bash\s*\n(.+?)\n\s*```', _re.DOTALL)
            for m in bash_block_pat.finditer(text):
                cmd = m.group(1).strip()
                # Only treat as tool call if it's a short single command (not a code example)
                if '\n' not in cmd or len(cmd) < 200:
                    _add_tool_call("bash", json.dumps({"command": cmd}))

        # Clean displayed text — remove all tool-call patterns
        clean_text = text
        for pat in [call_pattern, action_pattern, xml_tool_pat, invoke_pat, simple_xml_pat]:
            clean_text = pat.sub("", clean_text)
        # Remove function_calls wrapper, tool_result blocks, markdown code blocks used as tools
        clean_text = _re.sub(r'</?function_calls>', '', clean_text)
        clean_text = _re.sub(r'<tool_result>.*?</tool_result>', '', clean_text, flags=_re.DOTALL)
        clean_text = _re.sub(r'```(?:json|bash|python)\s*\{.*?\}\s*```', '', clean_text, flags=_re.DOTALL)
        if tool_calls:
            # If we found tool calls from bash blocks, remove those blocks from text
            clean_text = _re.sub(r'```bash\s*\n.+?\n\s*```', '', clean_text, flags=_re.DOTALL)
        clean_text = clean_text.strip()

        return {"text": clean_text, "tool_calls": tool_calls}

    # ── Client Management ───────────────────────────────────────────

    def _get_anthropic_client(self, provider: Provider):
        if provider.name in self._clients:
            return self._clients[provider.name]
        try:
            from anthropic import Anthropic
            key = provider.api_key
            is_oauth = isinstance(key, str) and "oat" in key[:15]
            if is_oauth:
                client = Anthropic(
                    auth_token=key,
                    default_headers={"anthropic-beta": "claude-code-20250219,oauth-2025-04-20"},
                )
            else:
                client = Anthropic(api_key=key)
            self._clients[provider.name] = client
            return client
        except Exception:
            return None

    def _get_openai_client(self, provider: Provider):
        if provider.name in self._clients:
            return self._clients[provider.name]

        # Standard OpenAI-compatible client
        try:
            from openai import OpenAI
            client = OpenAI(api_key=provider.api_key, base_url=provider.base_url,
                            max_retries=0, timeout=60)
            self._clients[provider.name] = client
            return client
        except ImportError:
            return None

    # ── Persistence ─────────────────────────────────────────────────

    def _save(self):
        """Save providers to disk."""
        JARVIS_HOME.mkdir(parents=True, exist_ok=True)
        data = {name: p.to_dict() for name, p in self._providers.items()}
        tmp = PROVIDERS_FILE.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        tmp.rename(PROVIDERS_FILE)
        os.chmod(PROVIDERS_FILE, 0o600)

    def _load(self):
        """Load providers from disk."""
        if not PROVIDERS_FILE.exists():
            return
        try:
            with open(PROVIDERS_FILE) as f:
                data = json.load(f)
            for name, d in data.items():
                self._providers[name] = Provider(**d)
        except Exception:
            pass

    def _load_env_providers(self):
        """Auto-register providers from .env / environment variables."""
        # Auto-register additional OpenAI-compatible providers from env
        for name, env_key in [("openai", "OPENAI_API_KEY"), ("xai", "XAI_API_KEY"),
                               ("together", "TOGETHER_API_KEY"), ("openrouter", "OPENROUTER_API_KEY")]:
            key = os.environ.get(env_key, "")
            if key and name not in self._providers:
                template = TEMPLATES.get(name, {})
                self._providers[name] = Provider(
                    name=name, type=template.get("type", "openai"),
                    api_key=key, base_url=template.get("base_url", ""),
                    model=template.get("default_model", ""),
                    models=template.get("models", []),
                    priority=len(self._providers), enabled=True,
                )

    def _load_claude_credentials(self):
        """Auto-register Claude from Anthropic API key."""
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if key and "claude" not in self._providers:
            template = TEMPLATES.get("claude", {})
            self._providers["claude"] = Provider(
                name="claude", type=template.get("type", "anthropic"),
                api_key=key, base_url=template.get("base_url", ""),
                model=template.get("default_model", ""),
                models=template.get("models", []),
                priority=0, enabled=True,  # Claude gets highest priority
            )

    def _detect_type(self, api_key: str) -> str:
        """Guess provider type from API key format."""
        if api_key.startswith(("sk-ant-", "anthropic-")):
            return "anthropic"
        return "openai"  # OpenAI-compatible is the safe default
