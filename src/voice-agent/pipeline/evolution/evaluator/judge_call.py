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
import random
import time
import urllib.error
import urllib.request


__all__ = ["judge_call", "JudgeError"]


logger = logging.getLogger("jarvis.evolution.judge_call")


_MAX_ATTEMPTS = 3
_BASE_BACKOFF_S = 1.0
_BACKOFF_SLEEP = time.sleep  # monkey-patchable for tests


class JudgeError(RuntimeError):
    """Wrapper for any judge-side failure (timeout, rate-limit, parse)."""


_KNOWN_ANTHROPIC = {
    "claude-sonnet-4-6", "claude-opus-4-7", "claude-haiku-4-5",
}
_KNOWN_DEEPSEEK = {
    "deepseek-v4-pro", "deepseek-v4-flash", "deepseek-chat",
}
_KNOWN_OPENAI = {"gpt-5", "gpt-5-mini", "openai/gpt-oss-120b"}


def _should_retry(http_code: int | None) -> bool:
    """Retry on 429 + transient 5xx, give up on other 4xx."""
    if http_code is None:
        return True  # network error — retry
    if http_code == 429:
        return True
    if 500 <= http_code <= 599:
        return True
    return False


def _backoff_seconds(attempt: int, retry_after: str | None) -> float:
    if retry_after:
        try:
            return max(float(retry_after), 0.0)
        except (TypeError, ValueError):
            pass
    return _BASE_BACKOFF_S * (2 ** attempt) + random.uniform(0, 0.25)


def _retrying_post(req: urllib.request.Request) -> bytes:
    """POST with up to _MAX_ATTEMPTS retries on transient failures.

    Returns the response body as bytes on success. Raises JudgeError
    if all attempts exhausted, or on a non-retryable 4xx.
    """
    last_error: Exception | None = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            last_error = e
            if not _should_retry(getattr(e, "code", None)):
                raise JudgeError(f"http {e.code}: {e}") from e
            retry_after = None
            if hasattr(e, "headers") and e.headers is not None:
                retry_after = e.headers.get("Retry-After")
            elif hasattr(e, "hdrs") and e.hdrs is not None:
                retry_after = e.hdrs.get("Retry-After")
            if attempt < _MAX_ATTEMPTS - 1:
                _BACKOFF_SLEEP(_backoff_seconds(attempt, retry_after))
                continue
        except urllib.error.URLError as e:
            last_error = e
            if attempt < _MAX_ATTEMPTS - 1:
                _BACKOFF_SLEEP(_backoff_seconds(attempt, None))
                continue
    raise JudgeError(f"all {_MAX_ATTEMPTS} attempts failed: {last_error}")


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
            "user-agent": "jarvis-evolution/1.0 (+judge_call.anthropic)",
        },
        method="POST",
    )
    try:
        body = _retrying_post(req)
        data = json.loads(body.decode("utf-8"))
        return data["content"][0]["text"]
    except (KeyError, json.JSONDecodeError) as e:
        raise JudgeError(f"anthropic parse failed: {e}") from e


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
            "User-Agent": "jarvis-evolution/1.0 (+judge_call.deepseek)",
        },
        method="POST",
    )
    try:
        body = _retrying_post(req)
        data = json.loads(body.decode("utf-8"))
        return data["choices"][0]["message"]["content"]
    except (KeyError, json.JSONDecodeError) as e:
        raise JudgeError(f"deepseek parse failed: {e}") from e


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
            "User-Agent": "jarvis-evolution/1.0 (+judge_call.openai)",
        },
        method="POST",
    )
    try:
        body = _retrying_post(req)
        data = json.loads(body.decode("utf-8"))
        return data["choices"][0]["message"]["content"]
    except (KeyError, json.JSONDecodeError) as e:
        raise JudgeError(f"openai parse failed: {e}") from e


def judge_call(model: str, prompt: str, *, max_tokens: int = 600) -> str:
    if model in _KNOWN_ANTHROPIC:
        return _call_anthropic(model, prompt, max_tokens)
    if model in _KNOWN_DEEPSEEK:
        return _call_deepseek(model, prompt, max_tokens)
    if model in _KNOWN_OPENAI:
        return _call_openai(model, prompt, max_tokens)
    raise ValueError(f"unknown judge model: {model!r}")
