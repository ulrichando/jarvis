"""JARVIS Claude Client — connect to Claude AI via Anthropic API.

Supports both:
- API keys (sk-ant-api03-...) → x-api-key header
- OAuth tokens (sk-ant-oat01-...) → Authorization: Bearer + beta headers

Auto-detects which model is available and uses the best one.
Reads fresh OAuth tokens from Claude Code credentials when available.
"""

import asyncio
import json
import os
from pathlib import Path
from anthropic import Anthropic

OAUTH_BETAS = ["claude-code-20250219", "oauth-2025-04-20", "interleaved-thinking-2025-05-14"]

# Models to try in order of preference (best first)
MODELS = [
    "claude-sonnet-4-20250514",
    "claude-haiku-4-5-20251001",
    "claude-3-haiku-20240307",
]

# Models that require extended thinking with OAuth
THINKING_MODELS = {"claude-sonnet-4-20250514", "claude-opus-4-20250514"}

_client = None
_best_model = None


def _is_oauth(key: str) -> bool:
    return isinstance(key, str) and "oat" in key[:15]


def _get_key() -> str | None:
    # 1. Fresh token from Claude Code credentials
    creds_path = Path.home() / ".claude" / ".credentials.json"
    if creds_path.exists():
        try:
            creds = json.loads(creds_path.read_text())
            oauth = creds.get("claudeAiOauth", {})
            token = oauth.get("accessToken")
            if token:
                return token
        except Exception:
            pass

    # 2. Vault
    try:
        from brain.vault.tokens import TokenVault
        vault = TokenVault()
        key = vault.get("claude") or vault.get("anthropic")
        if key:
            return key
    except Exception:
        pass

    # 3. Env vars
    return os.environ.get("ANTHROPIC_OAUTH_TOKEN", "") or os.environ.get("ANTHROPIC_API_KEY", "") or None


def _get_client():
    global _client
    if _client:
        return _client

    key = _get_key()
    if not key:
        return None

    if _is_oauth(key):
        _client = Anthropic(
            auth_token=key,
            default_headers={"anthropic-beta": ",".join(OAUTH_BETAS)},
        )
    else:
        _client = Anthropic(api_key=key)

    return _client


class ClaudeReasoner:

    def __init__(self):
        self._available = None

    def is_available(self) -> bool:
        if self._available is not None:
            return self._available
        self._available = _get_client() is not None
        return self._available

    @property
    def model(self) -> str:
        global _best_model
        if _best_model:
            return _best_model
        _best_model = MODELS[0]
        return _best_model

    def _needs_thinking(self, model: str) -> bool:
        """Check if model requires extended thinking (OAuth constraint)."""
        key = _get_key()
        return _is_oauth(key) and model in THINKING_MODELS

    async def query(
        self, user_input: str, system_prompt: str,
        history: list[dict] | None = None,
    ) -> str | None:
        client = _get_client()
        if not client:
            return None

        messages = []
        if history:
            for turn in history[-6:]:
                role = "assistant" if turn["role"] == "jarvis" else "user"
                messages.append({"role": role, "content": turn["content"][:500]})
        messages.append({"role": "user", "content": user_input})

        try:
            return await asyncio.to_thread(
                self._call, client, messages, system_prompt
            )
        except Exception:
            return None

    def _call(self, client, messages, system):
        global _best_model
        for model in MODELS:
            try:
                kwargs = dict(
                    model=model,
                    max_tokens=4096,
                    system=system,
                    messages=messages,
                )
                if self._needs_thinking(model):
                    kwargs["thinking"] = {"type": "enabled", "budget_tokens": 1024}

                r = client.messages.create(**kwargs)
                _best_model = model

                # Extract text (skip thinking blocks)
                for block in r.content:
                    if block.type == "text":
                        return block.text
                return ""
            except Exception:
                try:
                    # Some models/auth modes don't support system param
                    msgs = [{"role": "user", "content": f"[System: {system}]\n\n{messages[0]['content']}"}]
                    msgs.extend(messages[1:])
                    kwargs = dict(model=model, max_tokens=4096, messages=msgs)
                    if self._needs_thinking(model):
                        kwargs["thinking"] = {"type": "enabled", "budget_tokens": 1024}
                    r = client.messages.create(**kwargs)
                    _best_model = model
                    for block in r.content:
                        if block.type == "text":
                            return block.text
                    return ""
                except Exception:
                    continue
        return None

    async def query_with_tools(
        self, messages: list[dict], tools: list[dict], system: str = "",
    ) -> dict:
        client = _get_client()
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

        try:
            return await asyncio.to_thread(
                self._call_tools, client, messages, claude_tools, system
            )
        except Exception as e:
            return {"text": f"Claude error: {e}", "tool_calls": []}

    def _call_tools(self, client, messages, tools, system):
        global _best_model
        for model in MODELS:
            try:
                kwargs = dict(
                    model=model,
                    max_tokens=4096,
                    system=system,
                    messages=messages,
                    tools=tools if tools else None,
                )
                if self._needs_thinking(model):
                    kwargs["thinking"] = {"type": "enabled", "budget_tokens": 1024}

                r = client.messages.create(**kwargs)
                _best_model = model
                text, tool_calls = "", []
                for block in r.content:
                    if block.type == "text":
                        text += block.text
                    elif block.type == "tool_use":
                        tool_calls.append({"id": block.id, "name": block.name, "args": block.input})
                return {"text": text, "tool_calls": tool_calls}
            except Exception:
                continue
        return {"text": "", "tool_calls": []}
