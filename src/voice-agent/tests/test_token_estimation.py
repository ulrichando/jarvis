"""Tests for tools/token_estimation.py — ported from claude-code's
services/tokenEstimation.ts. Covers chars/4 approximation, message
list estimation, tool-schema estimation, pressure thresholds, and
Groq-pricing cost calculation.
"""
from __future__ import annotations

import pytest

from tools.token_estimation import (
    HARD_TOKENS,
    MAX_CONTEXT_TOKENS,
    WARN_TOKENS,
    context_pressure_state,
    cost_usd,
    estimate_messages,
    estimate_tokens,
    estimate_tools,
    preflight,
)


# ── estimate_tokens ──────────────────────────────────────────────────


def test_estimate_tokens_zero_on_empty():
    assert estimate_tokens("") == 0


def test_estimate_tokens_floor_one():
    assert estimate_tokens("a") == 1
    assert estimate_tokens("ab") == 1
    assert estimate_tokens("abc") == 1


def test_estimate_tokens_chars_per_token():
    # 16 chars → 4 tokens (chars/4)
    assert estimate_tokens("abcdefghijklmnop") == 4
    # 100 chars → 25 tokens
    assert estimate_tokens("x" * 100) == 25


# ── estimate_messages ────────────────────────────────────────────────


def test_estimate_messages_string_content():
    msgs = [{"role": "user", "content": "x" * 100}]
    # 25 token chars + 3 framing
    assert estimate_messages(msgs) == 28


def test_estimate_messages_multi_role():
    msgs = [
        {"role": "system", "content": "x" * 40},   # 10 + 3 = 13
        {"role": "user", "content": "x" * 80},     # 20 + 3 = 23
        {"role": "assistant", "content": "x" * 8}, # 2 + 3 = 5
    ]
    assert estimate_messages(msgs) == 41


def test_estimate_messages_empty_list():
    assert estimate_messages([]) == 0


def test_estimate_messages_handles_list_content():
    """OpenAI content blocks: [{"type": "text", "text": "..."}, ...]"""
    msgs = [{
        "role": "assistant",
        "content": [
            {"type": "text", "text": "x" * 40},
            {"type": "tool_use", "input": "x" * 20},
        ],
    }]
    # 10 (text) + 5 (input) + 3 (framing) = 18
    assert estimate_messages(msgs) == 18


def test_estimate_messages_handles_missing_content():
    msgs = [{"role": "user"}]  # no content key
    assert estimate_messages(msgs) == 3  # framing only


# ── estimate_tools ───────────────────────────────────────────────────


def test_estimate_tools_counts_schema():
    tools = [{
        "function": {
            "name": "bash",                       # 4 chars → 1 token
            "description": "x" * 40,              # 10 tokens
            "parameters": {"type": "object"},     # ~5 tokens (str repr)
        },
    }]
    n = estimate_tools(tools)
    assert n > 10  # at minimum the description
    assert n < 50  # not absurd


def test_estimate_tools_unwrapped_function_dict_works():
    """Some callers pass the function dict directly without wrapper."""
    tools = [{"name": "bash", "description": "x" * 40, "parameters": {}}]
    assert estimate_tools(tools) > 5


def test_estimate_tools_empty_list():
    assert estimate_tools([]) == 0


# ── context_pressure_state ───────────────────────────────────────────


def test_pressure_ok_under_warn():
    assert context_pressure_state(0) == "ok"
    assert context_pressure_state(WARN_TOKENS - 1) == "ok"


def test_pressure_warn_at_threshold():
    assert context_pressure_state(WARN_TOKENS) == "warn"
    assert context_pressure_state(HARD_TOKENS - 1) == "warn"


def test_pressure_hard_at_threshold():
    assert context_pressure_state(HARD_TOKENS) == "hard"
    assert context_pressure_state(MAX_CONTEXT_TOKENS) == "hard"


# ── cost_usd ─────────────────────────────────────────────────────────


def test_cost_known_model_llama_70b():
    # llama-3.3-70b-versatile: $0.59/M input, $0.79/M output
    cost = cost_usd("llama-3.3-70b-versatile", 1_000_000, 1_000_000)
    assert abs(cost - (0.59 + 0.79)) < 1e-9


def test_cost_zero_for_zero_tokens():
    assert cost_usd("llama-3.3-70b-versatile", 0, 0) == 0.0


def test_cost_unknown_model_returns_zero():
    """Don't blow up the telemetry write on a model rename."""
    assert cost_usd("imaginary-model-x", 100, 100) == 0.0


def test_cost_strips_groq_prefix():
    """Some labels include 'groq:' — pricing table uses bare model id."""
    bare = cost_usd("llama-3.3-70b-versatile", 1_000, 500)
    prefixed = cost_usd("groq:llama-3.3-70b-versatile", 1_000, 500)
    assert bare > 0
    assert abs(bare - prefixed) < 1e-12


def test_cost_realistic_voice_turn():
    """Sanity check: a typical voice turn (60K input, 200 output) on
    llama-3.3-70b lands at ~$0.035 — the 98 KB system prompt is the
    dominant cost driver. Documented here so it's visible in CI:
    a 100-turn dogfood session burns about $3.50 on llama-3.3-70b
    (which is the main reason cost-tracking + auto-compact matter)."""
    cost = cost_usd("llama-3.3-70b-versatile", 60_000, 200)
    # 60_000 * 0.59/M = 0.0354; 200 * 0.79/M = 0.000158; total ~0.0356
    assert 0.03 < cost < 0.04


# ── preflight ────────────────────────────────────────────────────────


def test_preflight_returns_full_breakdown():
    result = preflight(
        system_prompt="x" * 4_000,           # ~1000 tokens
        chat_ctx_messages=[
            {"role": "user", "content": "x" * 400},  # ~100+3
            {"role": "assistant", "content": "x" * 200},  # ~50+3
        ],
        tools=[{"function": {"name": "bash", "description": "x" * 200, "parameters": {}}}],
    )
    assert "estimated_tokens" in result
    assert "pressure" in result
    assert "breakdown" in result
    assert result["breakdown"]["system"] == 1000
    assert result["breakdown"]["chat_ctx"] > 100
    assert result["breakdown"]["tools"] > 0
    assert result["estimated_tokens"] == sum(result["breakdown"].values())
    assert result["pressure"] == "ok"


def test_preflight_warn_pressure(caplog):
    """Approaching WARN_TOKENS triggers a logger.warning."""
    import logging
    caplog.set_level(logging.WARNING, logger="jarvis.token_estimation")

    big_prompt = "x" * (WARN_TOKENS * 4 + 10)  # crosses WARN
    result = preflight(
        system_prompt=big_prompt,
        chat_ctx_messages=[],
        tools=[],
        label="test",
    )
    assert result["pressure"] in ("warn", "hard")
    # logger.warning() fired
    assert any("token-estimation" in r.message and "pressure=" in r.message for r in caplog.records)


def test_preflight_no_warn_when_ok(caplog):
    """Below WARN — silent."""
    import logging
    caplog.set_level(logging.WARNING, logger="jarvis.token_estimation")

    result = preflight(
        system_prompt="x" * 1000,
        chat_ctx_messages=[],
        tools=[],
    )
    assert result["pressure"] == "ok"
    assert not any("token-estimation" in r.message for r in caplog.records)


# ── pricing-table coverage ───────────────────────────────────────────


@pytest.mark.parametrize("model", [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "qwen3-32b",
    "qwen/qwen3-32b",
    "llama-4-scout",
    "openai/gpt-oss-120b",
    "deepseek-v4-pro",
])
def test_pricing_table_has_every_dispatcher_model(model):
    """Every model the LLM dispatcher routes to MUST have a price.
    A NULL cost in telemetry would be silent waste — better to
    catch the missing entry in CI."""
    cost = cost_usd(model, 1_000, 100)
    assert cost > 0, f"model {model!r} missing from pricing table"
