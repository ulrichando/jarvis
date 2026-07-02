"""Regression guard for the 2026-07-02 DeepSeek voice-model migration.

The bare `deepseek-chat` API alias is discontinued 2026-07-24, and V4-Flash
defaults to THINKING mode (6-47s TTFT + rejects tool_choice=required). So every
DeepSeek SPEECH_MODELS entry must (a) target the explicit `deepseek-v4-flash`
id and (b) force non-thinking via extra_body. This test fails if a future edit
reverts either — which would re-break voice latency / tool-forced routes.
"""
from __future__ import annotations

import pytest

# Build needs a non-empty key (the plugin validates presence, not validity).
@pytest.fixture(autouse=True)
def _fake_key(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key-not-real")


@pytest.mark.parametrize("model_id", ["deepseek-chat", "deepseek-v4-flash", "deepseek-chat-v3"])
def test_deepseek_speech_models_are_explicit_v4flash_non_thinking(model_id):
    from providers.llm import SPEECH_MODELS

    llm = SPEECH_MODELS[model_id]["build"]()
    opts = getattr(llm, "_opts", None)
    assert opts is not None, f"{model_id}: no _opts on built LLM"
    # (a) explicit alias-proof id, NOT the discontinued bare "deepseek-chat" alias
    assert getattr(opts, "model", None) == "deepseek-v4-flash", (
        f"{model_id}: builds {getattr(opts,'model',None)!r}, expected 'deepseek-v4-flash' "
        "(the bare 'deepseek-chat' alias is discontinued 2026-07-24)"
    )
    # (b) non-thinking forced (voice: fast TTFT + tool_choice=required works)
    assert getattr(opts, "extra_body", None) == {"thinking": {"type": "disabled"}}, (
        f"{model_id}: thinking not disabled — voice would get 6-47s TTFT + tool 400s"
    )


def test_non_thinking_constant_shape():
    from providers.llm import _DEEPSEEK_NON_THINKING

    assert _DEEPSEEK_NON_THINKING == {"thinking": {"type": "disabled"}}
