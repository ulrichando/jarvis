"""Single boundary for judge LLM calls — keeps tests mockable.

The proposer LLM (Groq llama-3.1-8b-instant) is NEVER routed here.
Anthropic Sonnet 4.6, DeepSeek v4-pro, and OpenAI GPT-5 are the
intended judges; any one can be unreachable (breaker open) and the
caller stage decides how to handle that.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request


__all__ = ["judge_call", "JudgeError"]


logger = logging.getLogger("jarvis.evolution.judge_call")


class JudgeError(RuntimeError):
    """Wrapper for any judge-side failure (timeout, rate-limit, parse)."""


_KNOWN_ANTHROPIC = {
    "claude-sonnet-4-6", "claude-opus-4-7", "claude-haiku-4-5",
}
_KNOWN_DEEPSEEK = {
    "deepseek-v4-pro", "deepseek-v4-flash", "deepseek-chat",
}
_KNOWN_OPENAI = {"gpt-5", "gpt-5-mini", "openai/gpt-oss-120b"}


def _call_anthropic(model: str, prompt: str, max_tokens: int) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise JudgeError("ANTHROPIC_API_KEY missing")
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["content"][0]["text"]
    except (urllib.error.URLError, KeyError, json.JSONDecodeError) as e:
        raise JudgeError(f"anthropic call failed: {e}") from e


def _call_deepseek(model: str, prompt: str, max_tokens: int) -> str:
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        raise JudgeError("DEEPSEEK_API_KEY missing")
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
    }
    req = urllib.request.Request(
        "https://api.deepseek.com/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"]
    except (urllib.error.URLError, KeyError, json.JSONDecodeError) as e:
        raise JudgeError(f"deepseek call failed: {e}") from e


def _call_openai(model: str, prompt: str, max_tokens: int) -> str:
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise JudgeError("OPENAI_API_KEY missing")
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"]
    except (urllib.error.URLError, KeyError, json.JSONDecodeError) as e:
        raise JudgeError(f"openai call failed: {e}") from e


def judge_call(model: str, prompt: str, *, max_tokens: int = 600) -> str:
    if model in _KNOWN_ANTHROPIC:
        return _call_anthropic(model, prompt, max_tokens)
    if model in _KNOWN_DEEPSEEK:
        return _call_deepseek(model, prompt, max_tokens)
    if model in _KNOWN_OPENAI:
        return _call_openai(model, prompt, max_tokens)
    raise ValueError(f"unknown judge model: {model!r}")
