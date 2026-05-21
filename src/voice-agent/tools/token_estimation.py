"""Token estimation + cost tracking — ports claude-code's
services/tokenEstimation.ts + cost-tracker.ts to voice-agent.

Pre-flight token counting before LLM calls + per-turn cost
accounting. Tokenization uses the chars-per-token approximation
(~4) that claude-code falls back to when the exact tokenizer
isn't loaded — accurate enough for context-pressure decisions
where the threshold has 13K of headroom.

Voice-side use:
  - At the start of each turn, estimate the system prompt + chat_ctx
    + tool-schema tokens. If the estimate exceeds the warn threshold,
    log a `[token-estimation] pressure=warn` line so the operator
    can see context filling up before the worker actually trips
    Groq's 128K context limit.
  - At the end of each turn, the LLM returns exact input/output
    token counts in `usage`. Convert to USD via the pricing table
    and write to turn_telemetry.db's cost_usd column.

Why both estimate + exact:
  - Estimate is FREE (no API roundtrip) and runs on every turn.
  - Exact comes from the LLM response and is what we charge.
  - Difference between the two reveals tokenizer drift and lets
    us tune _CHARS_PER_TOKEN over time.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("jarvis.token_estimation")

# Average chars per token. Claude-code uses 4 (gpt-style); llama
# tokenizes English at ~3.5-4.5 chars/token depending on register.
# Conservative side: under-estimate produces more conservative
# warn triggers — the tradeoff is fine for headroom checks.
_CHARS_PER_TOKEN = 4

# llama-3.3-70b-versatile context window per Groq's specs.
# Other Groq models on the dispatcher have similar or larger windows.
MAX_CONTEXT_TOKENS = 128_000

# Warn at ~78% of context (28K headroom for output + safety margin).
WARN_TOKENS = 100_000

# Emergency: trigger context cleanup. ~90% of context.
HARD_TOKENS = 115_000

# Per-million-token pricing across all supervisor + subagent LLMs JARVIS
# calls. USD; format: model_id -> (input $/1M, output $/1M). Rates as of
# 2026-05-17 public list prices. Verify against provider pricing pages
# before any major spend audit:
#   Groq:      https://console.groq.com/docs/models
#   Anthropic: https://www.anthropic.com/pricing
#   OpenAI:    https://openai.com/api/pricing/
#   DeepSeek:  https://api-docs.deepseek.com/quick_start/pricing
#   Google:    https://ai.google.dev/gemini-api/docs/pricing
#
# Pre-2026-05-17: only Groq + DeepSeek + Kimi entries existed. Result:
# 173 of 196 recent turns had cost_usd=NULL because Anthropic + OpenAI
# + Google models had no rates to multiply tokens against (global
# review §P0-17 / 2026-05-17 plan §P0-OBS-1).
#
# Rates that include prompt-caching: the dict captures NON-cached rates;
# log_turn() multiplies (input_tokens - prompt_cached_tokens) at the
# full rate and prompt_cached_tokens at a separate cache rate via
# _CACHE_READ_DISCOUNT below.
_PRICING_USD_PER_1M: dict[str, tuple[float, float]] = {
    # ── Groq ────────────────────────────────────────────────────────
    # TASK-tier supervisor model.
    "llama-3.3-70b-versatile":  (0.59, 0.79),
    # BANTER-tier (chitchat / fast).
    "llama-3.1-8b-instant":     (0.05, 0.08),
    # REASONING-tier.
    "qwen3-32b":                (0.29, 0.59),
    "qwen/qwen3-32b":           (0.29, 0.59),
    # EMOTIONAL-tier.
    "llama-4-scout":            (0.11, 0.34),
    "meta-llama/llama-4-scout-17b-16e-instruct": (0.11, 0.34),
    # Subagent code reviewer / experimental.
    "openai/gpt-oss-120b":      (0.15, 0.60),
    # Kimi modes (gated behind JARVIS_KIMI_VOICE_EXPERIMENTAL).
    "kimi-k2.6":                (0.30, 1.50),
    # ── DeepSeek ────────────────────────────────────────────────────
    # v4-pro retired 2026-05-16 but the rate stays in case archival
    # telemetry needs to be re-costed.
    "deepseek-v4-pro":          (0.27, 1.10),
    "deepseek-v4-flash":        (0.14, 0.28),
    "deepseek-chat":            (0.27, 1.10),  # V3 main chat model
    "deepseek-reasoner":        (0.55, 2.19),  # R1-style reasoning model
    # ── Anthropic ───────────────────────────────────────────────────
    # Voice supervisor + screen-share subagent. Caching="ephemeral"
    # set on these; verify hit rate via prompt_cached_tokens column.
    "claude-haiku-4-5":         (1.00,  5.00),
    "claude-haiku-4-5-20251001": (1.00,  5.00),
    "claude-sonnet-4-6":        (3.00, 15.00),
    "claude-opus-4-7":          (15.00, 75.00),
    # Legacy / canonical-ID aliases (livekit-plugins-anthropic prefixes).
    "anthropic:claude-haiku-4-5":  (1.00,  5.00),
    "anthropic:claude-sonnet-4-6": (3.00, 15.00),
    "anthropic:claude-opus-4-7":  (15.00, 75.00),
    # ── OpenAI ──────────────────────────────────────────────────────
    # GPT-5 family added 2026-05-15. Voice supervisor candidates.
    "gpt-5-nano":               (0.05,  0.20),
    "gpt-5-mini":               (0.15,  0.60),
    "gpt-5":                    (1.25,  5.00),
    "gpt-5.1":                  (2.00,  8.00),
    "gpt-5-pro":                (10.00, 30.00),
    # Legacy 4o for any straggler telemetry.
    "gpt-4o":                   (2.50, 10.00),
    "gpt-4o-mini":              (0.15,  0.60),
    # ── Google Gemini ───────────────────────────────────────────────
    # Used for vision subagent + screen-share live (gemini-2.5-flash-lite).
    "gemini-2.5-flash":         (0.30,  2.50),
    "gemini-2.5-flash-lite":    (0.10,  0.40),
    "gemini-2.5-pro":           (1.25, 10.00),
    # Vendor-prefixed alias.
    "google:gemini-2.5-flash":      (0.30,  2.50),
    "google:gemini-2.5-flash-lite": (0.10,  0.40),
    "google:gemini-2.5-pro":        (1.25, 10.00),
}

# Cache-read discount multiplier per provider. Cached input tokens are
# billed at <input rate> * <discount>. Hardcoded by family because the
# pricing-page math differs per provider (Anthropic 10%, OpenAI 10%,
# DeepSeek ~2%, Groq ~50% but only on some models). Lookup by id prefix.
_CACHE_READ_DISCOUNT: dict[str, float] = {
    "claude-":         0.10,  # Anthropic ephemeral cache: 10% of input
    "anthropic:":      0.10,
    "gpt-5":           0.10,  # OpenAI auto-cache (1024+ tokens, 5-24h): 10%
    "gpt-4o":          0.50,  # 4o cache discount is smaller
    "deepseek-":       0.02,  # DeepSeek context cache: 2% of input
    "gemini-2.5-":     0.10,  # Gemini implicit cache: 10%
    "google:gemini-":  0.10,
    "llama-":          0.50,  # Groq KV cache: 50% (only on some models)
    "qwen":            0.50,
    "kimi-":           0.50,
}


def cache_read_rate(model_id: str) -> float:
    """Return the cache-read multiplier for `model_id`. Defaults to 1.0
    (no discount — full input rate) when no prefix matches."""
    for prefix, discount in _CACHE_READ_DISCOUNT.items():
        if model_id.startswith(prefix):
            return discount
    return 1.0


def estimate_tokens(text: str) -> int:
    """Rough token count for a single string. Returns at least 1
    for non-empty input so an empty-string edge case doesn't
    underflow downstream math."""
    if not text:
        return 0
    return max(1, len(text) // _CHARS_PER_TOKEN)


def estimate_messages(messages: list[dict]) -> int:
    """Estimate tokens for a chat-completions message list.

    Each message dict is expected to be {"role": ..., "content": ...}.
    Adds ~3 tokens per message overhead for role + chat-template
    framing, matching how OpenAI recommends counting their tokens.
    Tool messages and assistant tool-call blocks count their content
    fields including any embedded JSON.

    Args:
        messages: list of dicts with at minimum a 'content' key.

    Returns:
        Estimated total token count for the message list.
    """
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    # OpenAI-style content blocks: {"type": "text", "text": "..."}
                    text = block.get("text") or block.get("content") or ""
                    if text:
                        total += estimate_tokens(str(text))
                    # Tool calls have arguments — count those too.
                    args = block.get("input") or block.get("arguments") or ""
                    if args:
                        total += estimate_tokens(str(args))
                else:
                    total += estimate_tokens(str(block))
        # Per-message framing overhead (role + start/end markers).
        total += 3
    return total


def estimate_tools(tools: list[dict]) -> int:
    """Estimate tokens for a tool-schemas list (the OpenAI tools
    array). Each tool's name + description + JSON-schema contribute.
    Important for voice — the supervisor exposes 40+ tools, each
    schema adds 100-300 tokens to every request."""
    total = 0
    for tool in tools:
        # Tool wrapper: {"type": "function", "function": {...}}
        fn = tool.get("function", tool)
        name = fn.get("name", "") or ""
        desc = fn.get("description", "") or ""
        params = fn.get("parameters", {}) or {}
        total += estimate_tokens(name)
        total += estimate_tokens(desc)
        # Parameters JSON is the biggest contributor for complex tools.
        total += estimate_tokens(str(params))
        total += 5  # framing overhead per tool
    return total


def context_pressure_state(token_count: int) -> str:
    """Classify a token count. Returns one of:
      - "ok"    — well under threshold
      - "warn"  — approaching context cap, consider trimming
      - "hard"  — emergency, trigger cleanup
    """
    if token_count >= HARD_TOKENS:
        return "hard"
    if token_count >= WARN_TOKENS:
        return "warn"
    return "ok"


def cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> float:
    """Compute the USD cost for an LLM call.

    Returns 0.0 if the model isn't in the pricing table — prevents
    cost-tracker writes from blocking on a model rename.

    Args:
        model: provider:model string (e.g. "llama-3.3-70b-versatile").
        input_tokens: prompt tokens (system + chat_ctx + tools).
        output_tokens: completion tokens (the supervisor's reply).
    """
    rates = _PRICING_USD_PER_1M.get(model)
    if rates is None:
        # Try stripping a "groq:" prefix that some labels include.
        if model.startswith("groq:"):
            rates = _PRICING_USD_PER_1M.get(model[5:])
    if rates is None:
        logger.debug(
            f"[cost] unknown model '{model}' — pricing returns 0; "
            f"add to _PRICING_USD_PER_1M to track"
        )
        return 0.0
    in_rate, out_rate = rates
    return (input_tokens / 1_000_000) * in_rate + (output_tokens / 1_000_000) * out_rate


def preflight(
    *,
    system_prompt: str,
    chat_ctx_messages: list[dict],
    tools: Optional[list[dict]] = None,
    label: str = "supervisor",
) -> dict:
    """Run a pre-flight token estimate before an LLM call.

    Returns a dict with:
      - estimated_tokens (int)
      - pressure ("ok" / "warn" / "hard")
      - breakdown {system: int, chat_ctx: int, tools: int}

    Logs a `[token-estimation]` line at WARN/HARD pressure so the
    operator can see context fill before Groq returns 413.
    """
    sys_tokens = estimate_tokens(system_prompt)
    ctx_tokens = estimate_messages(chat_ctx_messages or [])
    tool_tokens = estimate_tools(tools or [])
    total = sys_tokens + ctx_tokens + tool_tokens
    pressure = context_pressure_state(total)
    if pressure != "ok":
        logger.warning(
            f"[token-estimation] {label} pressure={pressure} "
            f"total={total} (system={sys_tokens} ctx={ctx_tokens} "
            f"tools={tool_tokens}) max={MAX_CONTEXT_TOKENS}"
        )
    return {
        "estimated_tokens": total,
        "pressure": pressure,
        "breakdown": {
            "system": sys_tokens,
            "chat_ctx": ctx_tokens,
            "tools": tool_tokens,
        },
    }
