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


# Anthropic models that speak the NATIVE computer_20251124 tool (beta header
# computer-use-2025-11-24) — verified against the computer-use docs 2026-07-04.
# Haiku 4.5 only has the older computer_20250124 surface → stays on SOM.
_NATIVE_CU_MARKERS = ("sonnet-5", "opus-4-8", "opus-4-7", "sonnet-4-6", "opus-4-6", "opus-4-5")


def native_anthropic_cu(model: str) -> bool:
    """True when this model drives Anthropic's native computer_20251124 tool
    instead of the custom SOM schema. Native buys the trained-for tool contract,
    the zoom action, and Anthropic's screenshot prompt-injection classifiers
    (those only run on the native beta). Kill-switch: JARVIS_CU_NATIVE_ANTHROPIC=0
    reverts every model to SOM (the pre-2026-07-04 behavior)."""
    if os.environ.get("JARVIS_CU_NATIVE_ANTHROPIC", "1").strip().lower() in {"0", "false", "off"}:
        return False
    if provider_for(model) != "anthropic":
        return False
    mid = (model or "").lower()
    return any(m in mid for m in _NATIVE_CU_MARKERS)


def make_adapter(model: str, system: str) -> CUAdapter:
    provider = provider_for(model)
    if provider == "openai":
        from .openai_adapter import OpenAICUAdapter
        return OpenAICUAdapter(model, system)
    if provider == "gemini":
        from .gemini_adapter import GeminiCUAdapter
        return GeminiCUAdapter(model, system)
    if native_anthropic_cu(model):
        from .anthropic_adapter import AnthropicNativeCUAdapter
        return AnthropicNativeCUAdapter(model, system)
    from .anthropic_adapter import AnthropicCUAdapter
    return AnthropicCUAdapter(model, system)
