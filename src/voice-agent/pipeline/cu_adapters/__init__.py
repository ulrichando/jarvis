"""cu_adapters — provider routing + factory for the computer-use loop."""
from __future__ import annotations

import os
from typing import Dict

from .base import CUAdapter


def provider_for(model: str) -> str:
    m = (model or "").lower()
    if m.startswith("gpt-") or m.startswith("o1") or m.startswith("o3"):
        return "openai"
    if m.startswith("gemini-"):
        return "gemini"
    return "anthropic"


def _key_for(provider: str) -> str:
    return {
        "anthropic": os.environ.get("ANTHROPIC_API_KEY", ""),
        "openai": os.environ.get("OPENAI_API_KEY", ""),
        "gemini": os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", ""),
    }.get(provider, "")


def available_providers() -> Dict[str, bool]:
    return {p: bool(_key_for(p)) for p in ("anthropic", "openai", "gemini")}


def make_adapter(model: str, system: str) -> CUAdapter:
    provider = provider_for(model)
    if provider == "openai":
        from .openai_adapter import OpenAICUAdapter
        return OpenAICUAdapter(model, system)
    if provider == "gemini":
        from .gemini_adapter import GeminiCUAdapter
        return GeminiCUAdapter(model, system)
    from .anthropic_adapter import AnthropicCUAdapter
    return AnthropicCUAdapter(model, system)
