"""Groq LLM resilience adapters + per-route dispatcher build.

Hoisted from `jarvis_agent.py` over 2026-05-10 (Steps 5b/5c/5d/5e
of the 10/10 refactor):

  - `BreakeredLLMStream` (5b) — first-chunk breaker gate + validation-
    error OPEN→CLOSED revert.
  - `BreakeredGroqLLM` (5c) — groq.LLM subclass that gates `chat()`
    through `LLM_BREAKER`, runs pre-flight token estimation, and
    hard-prunes the chat_ctx when the estimate is HARD-pressure.
  - `LAST_PREFLIGHT` (5c) — singleton dict the start-of-turn pre-flight
    writes and the end-of-turn telemetry write reads. Module-level
    so jarvis_agent can `from providers.llm import LAST_PREFLIGHT`
    and read it without a callback.
  - `ctx_items_token_estimate` / `prune_chat_ctx_for_budget` (5c) —
    pruning helpers used by the pre-flight branch and by the
    test_token_prune_2026_05_08 test suite.
  - `build_dispatching_llm` (5d) — assembles the per-route Maya-class
    DispatchingLLM (BANTER/TASK/REASONING/EMOTIONAL → distinct Groq
    variants, each wrapped in a FallbackAdapter(groq, deepseek-v4)).
  - `SPEECH_MODELS` registry + `read_speech_model` + `make_speech_llm`
    (5e) — tray-driven model picker for the user-selected supervisor
    LLM, reading the active id via `pipeline.settings.read_unified_setting`.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

from livekit.agents import APIConnectionError, APITimeoutError
from livekit.plugins import openai as lk_openai

# Anthropic — Claude Haiku 4.5 as a tray-selectable speech model and
# as the third fallback rung after Groq + DeepSeek. Optional dep: if
# the plugin isn't installed we skip the Anthropic surface entirely
# rather than crash boot.
try:
    from livekit.plugins import anthropic as lk_anthropic
    _ANTHROPIC_AVAILABLE = True
except Exception:  # pragma: no cover — install-only guard
    lk_anthropic = None
    _ANTHROPIC_AVAILABLE = False

from pipeline import specialty_routes as _specialty
from pipeline.dispatching_llm import DispatchingLLM
from pipeline.settings import read_unified_setting
from providers.local_model_picker import resolve_model_tag
from resilience import LLM_BREAKER
from resilience.circuit_breaker import (
    CircuitOpenError,
    STATE_CLOSED,
    STATE_OPEN,
)


logger = logging.getLogger("jarvis.llm")


__all__ = [
    # Tray-driven model picker
    "SPEECH_MODEL_FILE",
    "DEFAULT_SPEECH_MODEL",
    "SPEECH_MODELS",
    "read_speech_model",
    "make_speech_llm",
    # Token-aware pre-flight + pruning
    "LAST_PREFLIGHT",
    "ctx_items_token_estimate",
    "prune_chat_ctx_for_budget",
    # Breakered stream + LLM
    "BreakeredLLMStream",
    # Per-route dispatcher build
    "build_dispatching_llm",
]


# ── User-selected speech LLM (tray pick) ─────────────────────────────
# The tray UI writes the active id to ~/.jarvis/voice-model.
# entrypoint() calls `make_speech_llm()` once per job so a
# /voice-model POST + systemctl restart picks up the new file on the
# very next dispatch.
SPEECH_MODEL_FILE: Path = Path.home() / ".jarvis" / "voice-model"
# DEFAULT_SPEECH_MODEL is the model used when ~/.jarvis/voice-model
# is missing/unreadable. It ALSO defines the "no-pin baseline" for the
# pin-redesign logic in jarvis_agent.py:_build_llm_stack —
# `user_pinned_llm = active_speech_id != DEFAULT_SPEECH_MODEL`. If the
# user picks the same model that is the default, the pin logic treats
# that as "no pin" and per-route dispatcher defaults take over (BANTER
# = qwen3.6-27b, TASK = gpt-oss-120b, etc.). For the user to get
# OpenAI gpt-5-mini on the TASK route, the default must be SOMETHING
# OTHER THAN gpt-5-mini.
#
# Set to deepseek-chat on 2026-06-29 — was openai/gpt-oss-120b (Groq),
# removed in the full-Groq-eradication pass. Picked because it's an
# UNCONDITIONAL registry entry (always present) and is NOT a model the
# user is likely to pin, so the pin-detection baseline still works:
# - Picking any other model in the tray → user_pinned_llm=True →
#   build_dispatching_llm(task_override=<pick>).
# - Picking deepseek-chat (the default) → no pin → per-route dispatcher
#   (Anthropic primaries → DeepSeek fallback) takes over.
# - JARVIS_PIN_ALL_ROUTES=1 still works when a non-default is picked.
DEFAULT_SPEECH_MODEL: str = "deepseek-chat"

# IDs match the upstream model names verbatim so the registry stays
# legible. Each entry: (provider+model labels for display, factory
# building the LLM). Factories raise on missing API key — the
# read_speech_model() helper falls back to the default if so.
# Force DeepSeek V4 into NON-thinking mode on the OpenAI-compatible endpoint.
# V4 (flash + pro) DEFAULTS to thinking mode, which for voice means (a) 6–47s
# time-to-first-token (unusable) and (b) it rejects `tool_choice="required"`
# with HTTP 400 — the exact failure that broke JARVIS's tool-forced routes.
# `{"thinking": {"type": "disabled"}}` pins the instant non-reasoning path
# (~1.4s TTFT, tool_choice=auto works). Ref: api-docs.deepseek.com thinking-mode.
_DEEPSEEK_NON_THINKING = {"thinking": {"type": "disabled"}}

SPEECH_MODELS: dict[str, dict] = {
    # DeepSeek family — needs reasoning_content round-trip on
    # assistant tool-call messages, handled by deepseek_roundtrip.install()
    # at the top of jarvis_agent. v4-pro is best at tools; v4-flash trades
    # accuracy for ~30% latency reduction; deepseek-chat (V3) is the
    # non-thinking baseline (probe shows it never emits
    # reasoning_content even with the flag absent, so the patch's
    # capture path is dead for it).
    # NOTE (2026-07-02): the bare `deepseek-chat` / `deepseek-reasoner` API
    # aliases are DISCONTINUED 2026-07-24 (they currently point to V4-Flash
    # non-thinking / thinking). Both entries below now target the explicit,
    # alias-proof `deepseek-v4-flash` id + forced non-thinking — behavior-
    # identical to the old alias today, but survives the deprecation. The id
    # KEY stays "deepseek-chat" (it's DEFAULT_SPEECH_MODEL + the pin baseline).
    "deepseek-chat": {
        "label": "DeepSeek · V4-Flash (non-thinking, default)",
        "build": lambda: lk_openai.LLM(
            model="deepseek-v4-flash",
            api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
            base_url="https://api.deepseek.com/v1",
            temperature=0.6,
            extra_body=_DEEPSEEK_NON_THINKING,
        ),
    },
    "deepseek-v4-flash": {
        "label": "DeepSeek · V4-Flash (non-thinking)",
        "build": lambda: lk_openai.LLM(
            model="deepseek-v4-flash",
            api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
            base_url="https://api.deepseek.com/v1",
            temperature=0.6,
            # Without this, v4-flash defaults to THINKING → slow TTFW +
            # tool_choice=required 400s. Non-thinking is the voice-correct mode.
            extra_body=_DEEPSEEK_NON_THINKING,
        ),
    },
    # Same upstream model as "deepseek-chat", under a DISTINCT id so it is
    # pinnable: pin detection is `active_speech_id != DEFAULT_SPEECH_MODEL`
    # and deepseek-chat IS the default, so picking it un-pins and hands the
    # routes back to the per-route dispatcher (Anthropic primaries). Added
    # 2026-07-01: user wants V3-chat pinned all-routes for voice — v4-flash
    # (the only pinnable DeepSeek before this) is the latency-optimized
    # rung and audibly weaker in conversation (emote markup, language
    # drift, instruction slips). Deliberate exception to the "ids match
    # upstream names verbatim" convention above.
    "deepseek-chat-v3": {
        "label": "DeepSeek · V4-Flash (non-thinking, pinned)",
        "build": lambda: lk_openai.LLM(
            model="deepseek-v4-flash",
            api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
            base_url="https://api.deepseek.com/v1",
            temperature=0.6,
            extra_body=_DEEPSEEK_NON_THINKING,
        ),
    },
    # deepseek-v4-pro RETIRED 2026-05-16 per global review §P0-3.
    # Telemetry: 66 of 200 recent turns through v4-pro; 22 took >30s;
    # three took >700s; turn 160 produced a hallucinated Bosnian reply.
    # v4-flash + deepseek-chat (V3) remain available for users who
    # specifically want DeepSeek; the supervisor LLM cascade no longer
    # falls through to v4-pro automatically. Re-add a fixed version if
    # DeepSeek upstream resolves the long-tail latency.
    # Anthropic Claude — Haiku 4.5 is the fast, cheap voice-ready model.
    # Added 2026-05-11. `caching="ephemeral"` engages Anthropic's prompt
    # caching on the system prompt + chat_ctx prefix — typical 80-90 %
    # input-token discount on a stable JARVIS_INSTRUCTIONS preamble, so
    # the per-turn cost is dominated by the small output (~200 tokens).
    # Model id is passed as a raw string because the plugin's typed
    # ChatModels literal (1.5.8) predates Haiku 4.5; Anthropic's
    # /messages endpoint accepts the string verbatim.
    # Kimi K2.6 — Moonshot OpenAI-compat. DISABLED for voice as of
    # 2026-05-05: K2.6 spontaneously emits its built-in `web_search`
    # tool call even when not in `request.tools`, and Moonshot rejects
    # the request with `tool call validation failed: attempted to call
    # tool 'web_search' which was not in request.tools`. Every
    # supervisor turn fails on first content; circuit breaker opens;
    # user hears nothing. Gated behind JARVIS_KIMI_VOICE_EXPERIMENTAL=1
    # so it stays out of the tray picker by default — the flag is
    # there for the next attempt at proper integration (either
    # registering shim tools for K2.6's built-ins, or filtering them
    # from the request server-side).
}
# OpenAI proper (api.openai.com) — added 2026-05-15 as a fallback now
# that the Anthropic credit pool is exhausted. Same lk_openai plugin
# the DeepSeek entries use; default base_url is api.openai.com and the
# api_key is read from OPENAI_API_KEY by the plugin.
#
# Pick guidance:
#   - gpt-5-mini → voice default. Modern lineage, ~300-500 ms first
#     token, solid tool calling. Best balance for the "Jarvis, …" loop.
#   - gpt-5.1    → heavier sibling. ~500 ms more latency, but the best
#     tool-calling accuracy in this tier for multi-step delegations.
# Both gated on OPENAI_API_KEY so a key-less install doesn't pin a
# broken default.
if os.environ.get("OPENAI_API_KEY", ""):
    # The GPT-5 family rejects any non-default temperature (the API
    # returns `unsupported_value: 'temperature' does not support 0.6
    # with this model. Only the default (1) value is supported`), so we
    # omit the kwarg entirely. gpt-4o and earlier accept temperature.
    # Live failure 2026-05-15: gpt-5-mini build with temperature=0.6 →
    # every supervisor turn 400'd → fallback cascade to EdgeTTS.
    #
    # Tier guide (latency vs. capability):
    #   gpt-5-nano          → fastest + cheapest, weakest tool calling
    #   gpt-5-mini          → voice sweet spot (~300-500 ms first token)
    #   gpt-5               → base tier, ~50 % slower than mini but smarter
    #   gpt-5.1             → latest generation, best general quality
    #   gpt-5.1-chat-latest → pinned chat variant (auto-rolls)
    #   gpt-4o              → legacy classic, supports temperature, fast
    #
    # NOT registered: gpt-5-pro and gpt-5-codex are documented in the
    # /v1/models listing but the API rejects them on /v1/chat/completions
    # with `invalid_request_error: This model is only supported in
    # v1/responses and not in v1/chat/completions.` Verified 2026-05-15
    # against the user's key. lk_openai uses Chat Completions, so these
    # two would break every supervisor turn. Re-add when livekit-plugins-openai
    # ships a Responses-API adapter.
    SPEECH_MODELS["gpt-5-nano"] = {
        "label": "OpenAI · GPT-5 nano (fastest)",
        "build": lambda: lk_openai.LLM(model="gpt-5-nano"),
    }
    SPEECH_MODELS["gpt-5-mini"] = {
        "label": "OpenAI · GPT-5 mini",
        "build": lambda: lk_openai.LLM(model="gpt-5-mini"),
    }
    SPEECH_MODELS["gpt-5"] = {
        "label": "OpenAI · GPT-5",
        "build": lambda: lk_openai.LLM(model="gpt-5"),
    }
    SPEECH_MODELS["gpt-5.1"] = {
        "label": "OpenAI · GPT-5.1",
        "build": lambda: lk_openai.LLM(model="gpt-5.1"),
    }
    SPEECH_MODELS["gpt-5.1-chat-latest"] = {
        "label": "OpenAI · GPT-5.1 chat-latest",
        "build": lambda: lk_openai.LLM(model="gpt-5.1-chat-latest"),
    }
    # gpt-4o still accepts temperature, so we keep the matching 0.6
    # used across the other speech models.
    SPEECH_MODELS["gpt-4o"] = {
        "label": "OpenAI · GPT-4o (classic, supports temperature)",
        "build": lambda: lk_openai.LLM(model="gpt-4o", temperature=0.6),
    }

if _ANTHROPIC_AVAILABLE and os.environ.get("ANTHROPIC_API_KEY", ""):
    # Shared kwargs across Anthropic tiers — same `_strict_tool_schema`
    # and `max_tokens` discipline applies to every Claude speech model.
    # Pulled into a helper so adding a tier is a single `model=` line.
    #
    # Cache wiring (2026-05-23 refactor): we build `AnthropicCachedLLM`
    # instead of the bare `lk_anthropic.LLM` so the wrapper can place
    # `cache_control` on the STABLE prefix (between SOUL+INSTRUCTIONS+
    # skill_catalog and the volatile runtime_id+memory+breaker tail)
    # instead of on the LAST block of the joined prompt (the plugin's
    # default with `caching="ephemeral"`). The stable prefix is handed
    # to each wrapper after the prompt state assembles, via
    # `apply_stable_prefix_recursively`. We do NOT pass
    # `caching="ephemeral"` — the subclass owns cache_control placement
    # so the parent's auto-placement would be redundant noise.
    def _make_anthropic_speech_llm(model_id: str):
        from providers.anthropic_cached_llm import AnthropicCachedLLM
        return AnthropicCachedLLM(
            model=model_id,
            api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
            temperature=0.6,
            max_tokens=200,
            _strict_tool_schema=False,
        )

    SPEECH_MODELS["claude-haiku-4-5"] = {
        "label": "Anthropic · Claude Haiku 4.5",
        # `_strict_tool_schema=False` drops the `"strict": true` flag
        # from each Anthropic tool dict and uses the legacy-shape
        # parameters block. Defense-in-depth — does NOT by itself
        # fix the 2026-05-11 400 "tools.0.custom: For 'object' type,
        # additionalProperties must be explicitly set to false"
        # rejection (legacy schemas don't set additionalProperties at
        # all). The actual fix is the anthropic_strict_schema sanitizer
        # in jarvis_agent.py, which walks every nested object in the
        # schema tree and sets additionalProperties=false. Keeping
        # strict=False here removes Anthropic-side strict validation
        # of the model's tool args, matching the lenient stance the
        # strict_schema_relax patch already takes for Groq.
        #
        # max_tokens=200 caps response length at ~150 words / ~10s
        # of audio. Claude Haiku 4.5 over-elaborates philosophical
        # questions by default — live failure 2026-05-11 at 05:53 UTC:
        # "What's in your mind?" → 574-char monologue. Soft prompt
        # rules (the 30-word ceiling in supervisor.md) lose to
        # in-context priming from prior long replies; max_tokens is
        # a hard forcing function the model cannot ignore. Cost: a
        # genuinely long-form question ("explain MVCC in depth")
        # gets clipped mid-thought, but the recall-truncate logic
        # in pipeline/chat_ctx.py means even those don't poison
        # future sessions.
        "build": lambda: _make_anthropic_speech_llm("claude-haiku-4-5"),
    }
    # Sonnet 4.6 — middle tier. Better instruction-following and tool
    # selection than Haiku at the cost of ~2-3x per token + ~300ms
    # extra first-byte latency. Added 2026-05-11 in response to the
    # ongoing orchestration failures (skipped prerequisites, echoed
    # bailout phrases, wrong-tool selection) that Haiku's reasoning
    # capacity couldn't reliably avoid even with hardened prompts.
    # Same `_strict_tool_schema=False` + `caching="ephemeral"` discipline.
    SPEECH_MODELS["claude-sonnet-4-6"] = {
        "label": "Anthropic · Claude Sonnet 4.6",
        "build": lambda: _make_anthropic_speech_llm("claude-sonnet-4-6"),
    }
    # Opus 4.7 — most capable tier. ~10x Haiku cost, ~800ms extra
    # first-byte latency. Probably too slow for the "Yes?" pings but
    # available for tray selection when reasoning quality matters
    # more than latency (e.g., extended coding sessions through voice).
    SPEECH_MODELS["claude-opus-4-7"] = {
        "label": "Anthropic · Claude Opus 4.7",
        "build": lambda: _make_anthropic_speech_llm("claude-opus-4-7"),
    }
    # Opus 4.8 — current most-capable tier (2026-05-28). Same request surface
    # as 4.7 (adaptive thinking only, no sampling params) and Anthropic's
    # strongest computer-use / browser-agent model; the default escalation
    # target for the agentic routes (pipeline/specialty_routes.py).
    SPEECH_MODELS["claude-opus-4-8"] = {
        "label": "Anthropic · Claude Opus 4.8",
        "build": lambda: _make_anthropic_speech_llm("claude-opus-4-8"),
    }
# OpenRouter — one OpenAI-compatible endpoint (https://openrouter.ai/api/v1)
# that proxies hundreds of models. Only voice-suitable models are listed here:
# they must support streaming AND tool/function calls — models that are
# reasoning-only (no tool schema), chat-only (no streaming), or have >1 s
# first-byte latency under typical load should NOT be added. All entries
# gated on OPENROUTER_API_KEY so a key-less install doesn't break construction
# or the tray picker. The lk_openai plugin is used with a custom base_url,
# identical to the existing DeepSeek/Kimi entries in this file.
if os.environ.get("OPENROUTER_API_KEY", ""):
    # Each build lambda reads OPENROUTER_API_KEY fresh from the env at
    # call time (not an import-captured constant) so a rotated key is
    # picked up without restarting the worker.
    _OR_BASE = "https://openrouter.ai/api/v1"

    SPEECH_MODELS["openrouter/google/gemini-2.0-flash-001"] = {
        # Gemini 2.0 Flash via OpenRouter. Fast first byte (~300 ms),
        # streaming, solid tool calling. Good all-around voice model.
        "label": "OpenRouter · Gemini 2.0 Flash",
        "build": lambda: lk_openai.LLM(
            model="google/gemini-2.0-flash-001",
            api_key=os.environ.get("OPENROUTER_API_KEY", ""),
            base_url=_OR_BASE,
            temperature=0.6,
        ),
    }
    SPEECH_MODELS["openrouter/meta-llama/llama-3.3-70b-instruct"] = {
        # Llama 3.3 70B Instruct routed through OpenRouter. Mirrors the
        # Groq native entry but uses OpenRouter's edge for diversity.
        # Streaming + function calling confirmed on this model.
        "label": "OpenRouter · Llama 3.3 70B",
        "build": lambda: lk_openai.LLM(
            model="meta-llama/llama-3.3-70b-instruct",
            api_key=os.environ.get("OPENROUTER_API_KEY", ""),
            base_url=_OR_BASE,
            temperature=0.6,
        ),
    }
    SPEECH_MODELS["openrouter/anthropic/claude-haiku-4-5"] = {
        # Claude Haiku 4.5 via OpenRouter. Useful when the direct
        # Anthropic credit pool is exhausted but OpenRouter credits remain.
        # Streaming + tool calling, comparable latency to native Anthropic.
        "label": "OpenRouter · Claude Haiku 4.5",
        "build": lambda: lk_openai.LLM(
            model="anthropic/claude-haiku-4-5",
            api_key=os.environ.get("OPENROUTER_API_KEY", ""),
            base_url=_OR_BASE,
            temperature=0.6,
        ),
    }
    SPEECH_MODELS["openrouter/mistralai/mistral-small-3.2-24b-instruct"] = {
        # Mistral Small 3.2 24B — fast, cheap, and confirmed streaming +
        # tool-call capable. Good latency (~350 ms first token) for voice.
        "label": "OpenRouter · Mistral Small 3.2 24B",
        "build": lambda: lk_openai.LLM(
            model="mistralai/mistral-small-3.2-24b-instruct",
            api_key=os.environ.get("OPENROUTER_API_KEY", ""),
            base_url=_OR_BASE,
            temperature=0.6,
        ),
    }

# Google Gemini — explicit context caching via providers.gemini_llm.
# Added 2026-05-23 to make Gemini "ready" for a future operator
# JARVIS_{ROUTE}_MODEL=gemini-* flip without leaving caching unwired
# (Gemini does NOT auto-cache the way Anthropic/OpenAI/DeepSeek do —
# the system prompt would re-upload every turn without explicit
# CachedContent provisioning). See `providers/gemini_cache.py` module
# docstring for the full rationale.
#
# Gated on BOTH `GOOGLE_API_KEY` AND `livekit-plugins-google` being
# importable. The `build` lambda's import-from clause raises ImportError
# if the plugin is missing; `read_speech_model() / make_speech_llm` and
# `build_dispatching_llm` already handle this by falling back to the
# default speech model / route's Groq legacy primary.
#
# NOT pinned to any active route by this commit — operator opts in via
# JARVIS_{BANTER,TASK,REASONING,EMOTIONAL}_MODEL=gemini-2.5-flash (etc.)
# or via the tray-pick path.
def _build_gemini_speech_llm(model_id: str, temperature: float = 0.6):
    """Construct a GeminiCachedLLM speech-model entry.

    Raises ImportError when livekit-plugins-google isn't installed
    (caught by `make_speech_llm`'s try/except — falls back to
    DEFAULT_SPEECH_MODEL). Raises RuntimeError when GOOGLE_API_KEY
    is missing for the same fallback path."""
    api_key = os.environ.get("GOOGLE_API_KEY", "").strip()
    if not api_key:
        # Shape-match ImportError so the speech-LLM fallback
        # cascade in `make_speech_llm` treats it as 'this entry
        # isn't viable' (same behavior as the Anthropic gate above).
        raise ImportError(
            "GOOGLE_API_KEY missing — Gemini speech LLM unavailable"
        )
    # Lazy module import inside the lambda so SPEECH_MODELS dict
    # construction at import time doesn't pull in the plugin.
    from providers.gemini_llm import GeminiCachedLLM  # may raise ImportError
    return GeminiCachedLLM(
        model=model_id,
        api_key=api_key,
        temperature=temperature,
        # max_output_tokens caps the response length the same way
        # max_tokens=200 does for Anthropic above — Gemini is happy
        # to monologue without this cap, especially on Pro 2.5.
        max_output_tokens=200,
    )


SPEECH_MODELS["gemini-2.5-flash"] = {
    "label": "Google · Gemini 2.5 Flash (cached)",
    "build": lambda: _build_gemini_speech_llm("gemini-2.5-flash"),
}
SPEECH_MODELS["gemini-2.5-pro"] = {
    # Slower (+300-500 ms TTFW vs Flash) but materially stronger on
    # multi-step reasoning. Probably overkill for BANTER/EMOTIONAL but
    # appropriate for REASONING when an operator wants Gemini Pro
    # instead of Claude Sonnet 4.6.
    "label": "Google · Gemini 2.5 Pro (cached)",
    "build": lambda: _build_gemini_speech_llm("gemini-2.5-pro"),
}

if os.environ.get("JARVIS_KIMI_VOICE_EXPERIMENTAL", "0") == "1":
    SPEECH_MODELS["kimi-k2.6-instant"] = {
        "label": "Kimi · K2.6 Instant (experimental)",
        "build": lambda: lk_openai.LLM(
            model="kimi-k2.6",
            api_key=os.environ.get("KIMI_API_KEY", ""),
            base_url="https://api.moonshot.ai/v1",
            temperature=0.6,
        ),
    }
    SPEECH_MODELS["kimi-k2.6-thinking"] = {
        "label": "Kimi · K2.6 Thinking (experimental)",
        "build": lambda: lk_openai.LLM(
            model="kimi-k2.6",
            api_key=os.environ.get("KIMI_API_KEY", ""),
            base_url="https://api.moonshot.ai/v1",
            temperature=0.4,
        ),
    }
    SPEECH_MODELS["kimi-k2.6-agent"] = {
        "label": "Kimi · K2.6 Agent (experimental)",
        "build": lambda: lk_openai.LLM(
            model="kimi-k2.6",
            api_key=os.environ.get("KIMI_API_KEY", ""),
            base_url="https://api.moonshot.ai/v1",
            temperature=0.6,
        ),
    }
    SPEECH_MODELS["kimi-k2.6-swarm"] = {
        "label": "Kimi · K2.6 Swarm (experimental)",
        "build": lambda: lk_openai.LLM(
            model="kimi-k2.6",
            api_key=os.environ.get("KIMI_API_KEY", ""),
            base_url="https://api.moonshot.ai/v1",
            temperature=0.7,
        ),
    }

# Kimi K2.7 Code — current K2.7 model (verified live via Moonshot /v1/models,
# 2026-06-23). Added UNGATED for voice tool-calling per request. CAVEAT: K2.6
# broke the voice path (emitted its built-in web_search tool not in
# request.tools → Moonshot 400 → supervisor wedge → silent). K2.7-code is a
# code model and UNVERIFIED on the live voice tool-calling path — if JARVIS goes
# silent after selecting it, that's the same class of bug; switch back via tray.
SPEECH_MODELS["kimi-k2.7-code"] = {
    "label": "Kimi · K2.7 Code",
    "build": lambda: lk_openai.LLM(
        model="kimi-k2.7-code",
        api_key=os.environ.get("KIMI_API_KEY", ""),
        base_url="https://api.moonshot.ai/v1",
        temperature=0.3,
    ),
}


# ── Local models (Ollama / vLLM / llama.cpp via OpenAI-compat) ───────
# Always registered so the tray can PIN a local model regardless of the
# JARVIS_LOCAL_LLM_ENABLED rung-0 auto-injection. Each build lambda reads
# JARVIS_LOCAL_LLM_URL fresh, so pointing at a remote GPU box (e.g. the
# Windows server) is one env change + restart away. No API key needed for
# Ollama; `_strict_tool_schema=False` is MANDATORY (local servers reject
# OpenAI strict schema → JARVIS's 20+ tools silently break otherwise).
def _make_local_speech_llm(model_id: str):
    url = os.environ.get(
        "JARVIS_LOCAL_LLM_URL", "http://127.0.0.1:11434/v1"
    ).strip() or "http://127.0.0.1:11434/v1"
    key = os.environ.get("JARVIS_LOCAL_LLM_API_KEY", "ollama").strip() or "ollama"
    return lk_openai.LLM(
        model=model_id,
        base_url=url,
        api_key=key,
        temperature=0.6,
        _strict_tool_schema=False,
    )


SPEECH_MODELS["ollama/auto"] = {
    "label": "Local · Auto (best fit for this GPU)",
    "build": lambda: _make_local_speech_llm(resolve_model_tag("auto")),
}
SPEECH_MODELS["ollama/llama3.1:8b"] = {
    "label": "Local · Llama 3.1 8B (Ollama)",
    "build": lambda: _make_local_speech_llm("llama3.1:8b"),
}
SPEECH_MODELS["ollama/qwen3:14b"] = {
    "label": "Local · Qwen3 14B (Ollama)",
    "build": lambda: _make_local_speech_llm("qwen3:14b"),
}
# MoE locals (2026-06-18; measured on the 125 GB-RAM / AMD-iGPU Windows box).
# qwen3:30b-a3b is the sweet spot for a CPU-only box — 30B total but only ~3B
# active per token → fast tokens (~16 s incl. cold load), ~18 GB pull, verified
# tool-calling. USE THIS FOR LOCAL VOICE. gpt-oss:120b (OpenAI open-weight MoE,
# ~5B active, ~65 GB) also tool-calls correctly but on CPU it cold-loads in
# ~35 s and generates only ~8 tok/s — too slow for real-time voice; keep it for
# heavy non-interactive reasoning, not the voice path. No special backend config
# beyond ollama: both run over the /v1 OpenAI-compat endpoint with
# _strict_tool_schema=False. Select via ~/.jarvis/voice-model / JARVIS_LOCAL_LLM_MODEL
# (the tray submenu lists only the cloud models today).
SPEECH_MODELS["ollama/qwen3:30b-a3b"] = {
    "label": "Local · Qwen3 30B-A3B MoE (Ollama, ~3B active — fast on CPU)",
    "build": lambda: _make_local_speech_llm("qwen3:30b-a3b"),
}
SPEECH_MODELS["ollama/gpt-oss:120b"] = {
    "label": "Local · gpt-oss 120B MoE (Ollama, heavy — needs lots of RAM)",
    "build": lambda: _make_local_speech_llm("gpt-oss:120b"),
}


def read_speech_model() -> str:
    """Return the active speech model ID, or the default if unset/invalid.

    Reads the flat file written by the tray UI under `~/.jarvis/`
    (the unified-settings SDK is a thin wrapper over that file —
    there is no SQLite settings store)."""
    name = read_unified_setting("voice-model", SPEECH_MODEL_FILE)
    if name in SPEECH_MODELS:
        return name
    if name:
        logger.warning(
            f"unknown speech model {name!r}, falling back to {DEFAULT_SPEECH_MODEL}"
        )
    return DEFAULT_SPEECH_MODEL


def make_speech_llm() -> tuple[str, object]:
    """Build the chosen speech LLM, falling back to default on failure."""
    name = read_speech_model()
    try:
        llm = SPEECH_MODELS[name]["build"]()
        logger.info(f"speech LLM: {name} ({SPEECH_MODELS[name]['label']})")
        return name, llm
    except Exception as e:
        logger.error(
            f"failed to build speech LLM {name!r} ({e}); "
            f"falling back to {DEFAULT_SPEECH_MODEL}"
        )
        return DEFAULT_SPEECH_MODEL, SPEECH_MODELS[DEFAULT_SPEECH_MODEL]["build"]()


# ── Pre-flight singleton ─────────────────────────────────────────────
# Pre-flight estimate for the most recent supervisor LLM call.
# Module-level dict (one voice session per worker process) so the
# per-turn telemetry write at end-of-turn can read what the start-
# of-turn pre-flight saw.
#
# 2026-05-06: was a ContextVar — that broke because livekit-agents
# runs the LLM `chat()` and the session's per-turn telemetry write
# in DIFFERENT asyncio tasks, so the ContextVar reader always saw
# the default. A plain dict is correct here: one process = one
# session = one supervisor LLM at a time, so there's no concurrent
# overwrite to worry about.
LAST_PREFLIGHT: dict = {"tokens": None, "pressure": None, "model": None}


# Cache for the pre-flight chat_ctx + tools stringification. Each turn,
# only the LAST item in chat_ctx is new — items 0..N-2 are the same as
# last turn. Without a cache, stringifying 80 items × ~1k chars +
# system prompt of 134k bytes is ~200-500ms of blocking CPU on a
# 2-core box (global review §P0-18 / perf review). Cache by
# (id(chat_ctx), len(items), id(last_item)) — invalidates when a new
# item is appended (the id of items[-1] changes) or when the chat_ctx
# instance changes (new session). Tools rarely change so cached
# tools_str invalidates by `(id(tools), len(tools))`.
#
# Single-session-per-process assumption already documented above for
# LAST_PREFLIGHT — same applies here.
_PREFLIGHT_CACHE: dict = {
    "ctx_key": None,    # (id, len, id(last))
    "ctx_str": "",
    "tools_key": None,  # (id, len)
    "tools_str": "",
}


# ── Token-aware chat_ctx pruning helpers ─────────────────────────────

def ctx_items_token_estimate(items) -> int:
    """Cheap estimate of tokens consumed by a list of chat_ctx items.
    Mirrors the stringification used in `BreakeredGroqLLM.chat`'s
    pre-flight so the two stay in sync."""
    from tools.token_estimation import estimate_tokens
    s = ""
    for it in items:
        s += str(getattr(it, "content", it)) + "\n"
    return estimate_tokens(s)


def prune_chat_ctx_for_budget(chat_ctx, target_tokens: int):
    """Return a new ChatContext with oldest non-system items dropped
    until the estimate fits within `target_tokens`.

    Always preserves:
      - All system messages (the JARVIS_INSTRUCTIONS preamble — losing
        these is exactly the failure mode B in the 2026-05-08 audit:
        once the system prompt evaporates, the supervisor LLM
        hallucinates `delegate(role='summarize', ...)` for every turn).
      - Paired FunctionCall / FunctionCallOutput by call_id (dropping
        one without the other produces a 4xx from the API: a tool
        result with no preceding call is invalid).

    Returns the original chat_ctx unchanged when no pruning is needed
    (estimate already fits) or when chat_ctx is empty.
    """
    try:
        from livekit.agents.llm import ChatContext, ChatMessage
    except Exception:
        return chat_ctx

    items = list(getattr(chat_ctx, "items", None) or [])
    if not items:
        return chat_ctx

    if ctx_items_token_estimate(items) <= target_tokens:
        return chat_ctx

    # Mark which indices are protected (system messages always kept).
    is_system = [
        isinstance(it, ChatMessage)
        and getattr(it, "role", None) == "system"
        for it in items
    ]

    # Build call_id -> indices map so we drop pairs together.
    call_id_to_indices: dict[str, list[int]] = {}
    for i, it in enumerate(items):
        cid = getattr(it, "call_id", None)
        if cid:
            call_id_to_indices.setdefault(cid, []).append(i)

    # Drop oldest non-system items, expanding to pair-mates, until
    # the remaining items fit. Scan from the front (oldest) so the
    # most recent context survives — that's where the user's current
    # request and recent tool results live.
    drop: set[int] = set()
    for i, it in enumerate(items):
        if is_system[i] or i in drop:
            continue
        # Drop this item AND its pair (if any).
        candidates = {i}
        cid = getattr(it, "call_id", None)
        if cid:
            for j in call_id_to_indices.get(cid, []):
                if not is_system[j]:
                    candidates.add(j)
        # Don't drop system items.
        candidates = {k for k in candidates if not is_system[k]}
        drop |= candidates
        kept = [t for k, t in enumerate(items) if k not in drop]
        if ctx_items_token_estimate(kept) <= target_tokens:
            break

    pruned = [t for k, t in enumerate(items) if k not in drop]
    return ChatContext(items=pruned)


class BreakeredLLMStream:
    """Wraps a livekit-agents LLMStream so the first __anext__ goes
    through the supplied `breaker`. After the first chunk arrives we
    pass through untouched — the breaker only protects against cold
    starts (DNS / first-byte latency), not mid-stream stalls.

    Mirrors the FallbackAdapter contract: convert `CircuitOpenError`
    and `asyncio.TimeoutError` to `APIConnectionError` /
    `APITimeoutError` so livekit-agents cascades to the next LLM in
    the FallbackAdapter chain (typically DeepSeek)."""

    def __init__(self, inner, breaker):
        self._inner = inner
        self._breaker = breaker
        self._first = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        # First chunk only goes through the breaker — it protects cold
        # starts (DNS, TCP handshake, time-to-first-byte). Mid-stream
        # stalls (LLM hangs at chunk 5 of 20) are NOT protected; that
        # would require per-chunk timeout tracking. FallbackAdapter's
        # retry_on_chunk_sent=False default also won't cascade
        # mid-stream, so the boundary is consistent across the stack.
        # TODO: mid-stream stall protection if production telemetry
        # shows it's worth the complexity.
        if self._first:
            self._first = False
            try:
                return await self._breaker.call(self._inner.__anext__)
            except CircuitOpenError as e:
                raise APIConnectionError() from e
            except asyncio.TimeoutError:
                raise APITimeoutError() from None
            except Exception as e:
                # Schema-validation errors are NOT a "provider is down"
                # signal — they're "the LLM emitted a malformed tool
                # call." Live-observed 2026-05-04 (Groq llama-3.3,
                # `Failed to call a function`) and again 2026-05-05
                # (Kimi K2.6, `tool call validation failed: attempted
                # to call tool 'web_search'`). Each pair of failures
                # tripped fail_threshold=2; breaker stayed open and
                # every following turn fell to slower DeepSeek path.
                # From the user's seat: "I can't have a normal
                # conversation."
                #
                # Fix: un-count validation-error failures and revert
                # OPEN→CLOSED. tool_name_sanitizer + downstream
                # recovery handle the malformation; the breaker only
                # protects against transport-layer outages.
                #
                # The error we catch here is wrapped by livekit-agents
                # (inference/llm.py raises APIConnectionError from
                # the underlying openai.APIError), so the validation
                # text only lives on `e.__cause__` / `e.__context__`.
                # Walk the chain rather than checking str(e), which
                # is just "Connection error.".
                _msgs: list[str] = []
                _cur: BaseException | None = e
                _seen: set[int] = set()
                while _cur is not None and id(_cur) not in _seen:
                    _seen.add(id(_cur))
                    _msgs.append(str(_cur).lower())
                    _cur = _cur.__cause__ or _cur.__context__
                err_msg = " | ".join(_msgs)
                is_validation_error = (
                    "failed to call a function" in err_msg
                    or "tool call validation failed" in err_msg
                    or "failed_generation" in err_msg
                    or "please adjust your prompt" in err_msg
                )
                if is_validation_error:
                    if self._breaker.failures > 0:
                        self._breaker.failures -= 1
                    if (
                        self._breaker.state == STATE_OPEN
                        and self._breaker.failures < self._breaker.fail_threshold
                    ):
                        self._breaker.state = STATE_CLOSED
                        logger.info(
                            "[breaker:llm] reverted OPEN→closed "
                            "(validation error, not transport)"
                        )
                raise
        return await self._inner.__anext__()

    async def aclose(self):
        if hasattr(self._inner, "aclose"):
            await self._inner.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.aclose()

    # Some livekit code paths poke .ctx, .messages, etc. on the
    # underlying stream. Forward attribute access by default so we're
    # transparent to the caller.
    def __getattr__(self, name):
        return getattr(self._inner, name)


# ── Per-route DispatchingLLM build ───────────────────────────────────

# Anthropic primary defaults — overridable per route via env. Chosen
# 2026-05-23 because Anthropic + caching="ephemeral" delivers ~700 ms
# TTFW on warm cache vs ~2 s on Groq (no caching).
#
# Haiku 4.5 for the three high-frequency routes; Sonnet 4.6 only for
# REASONING (rare, multi-step). Temperature mirrors the Groq legacy
# per-route value (EMOTIONAL keeps 0.7 for warmth).
#
# 2026-05-24: TASK_* sub-route defaults source from
# pipeline.specialty_routes (per the pre-TTS confab gate design).
# Temps fixed to 0.6 (task-shaped work). The legacy TASK row stays
# for backwards-compat with code paths that still resolve "TASK".
_ANTH_DEFAULT_PER_ROUTE: dict[str, tuple[str, str, float]] = {
    # route          → (env-var,                       default-model,        temp)
    "BANTER":         ("JARVIS_BANTER_MODEL",          "claude-haiku-4-5",  0.6),
    "TASK":           ("JARVIS_TASK_MODEL",            "claude-haiku-4-5",  0.6),
    "REASONING":      ("JARVIS_REASONING_MODEL",       "claude-sonnet-4-6", 0.6),
    "EMOTIONAL":      ("JARVIS_EMOTIONAL_MODEL",       "claude-haiku-4-5",  0.7),
    "TASK_DESKTOP":   ("JARVIS_TASK_DESKTOP_MODEL",    "claude-sonnet-4-6", 0.6),
    "TASK_BROWSER":   ("JARVIS_TASK_BROWSER_MODEL",    "claude-sonnet-4-6", 0.6),
    "TASK_CODE":      ("JARVIS_TASK_CODE_MODEL",       "deepseek-v4-flash", 0.6),
    "TASK_FILES":     ("JARVIS_TASK_FILES_MODEL",      "claude-haiku-4-5",  0.6),
    "TASK_OTHER":     ("JARVIS_TASK_OTHER_MODEL",      "claude-haiku-4-5",  0.6),
}


def resolve_route_primary_model(route: str) -> str:
    """Public: resolve a route's PRIMARY supervisor model id (rung-1 only).

    Lookup order mirrors build_dispatching_llm's nested _resolve_route_model:
      1. per-route env override (JARVIS_TASK_DESKTOP_MODEL etc.)
      2. legacy JARVIS_TASK_MODEL for TASK_* routes
      3. specialty_routes spec default, else the _ANTH_DEFAULT_PER_ROUTE default.
    Returns '' for an unknown route. Used by the computer_use vision gate."""
    entry = _ANTH_DEFAULT_PER_ROUTE.get(route)
    if entry is None:
        return ""
    env_var, default_model, _temp = entry
    override = os.environ.get(env_var, "").strip()
    if override:
        return override
    legacy_task = os.environ.get("JARVIS_TASK_MODEL", "").strip()
    if legacy_task and route.startswith("TASK_"):
        return legacy_task
    try:
        spec_default = _specialty.get_primary_model(route)
    except Exception:
        spec_default = None
    return spec_default or default_model


# Routes where the supervisor MUST call a tool — narration-only replies on
# these are categorically confab. Set via tool_choice="required" on the
# primary LLM construction; Anthropic maps "required" → {"type":"any"} per
# anthropic_cached_llm.py:212-213. BANTER/EMOTIONAL/REASONING/TASK_OTHER
# can legitimately be text-only (chitchat, thinking-out-loud, follow-up
# questions), so the model decides on those.
_TOOL_FORCED_ROUTES = frozenset({
    "TASK_DESKTOP", "TASK_BROWSER", "TASK_CODE", "TASK_FILES",
})


def _tool_choice_for_route(route: str) -> Optional[str]:
    """Return "required" for action routes (force a tool call), None
    otherwise (model decides between tool call vs text reply)."""
    return "required" if route in _TOOL_FORCED_ROUTES else None


def _probe_local_llm(
    base_url: str,
    model: str,
    api_key: str,
    timeout_s: float,
) -> tuple[bool, str]:
    """Return whether the configured local OpenAI-compatible LLM is real.

    Constructing ``lk_openai.LLM`` does not contact Ollama/vLLM, so a stale
    ``JARVIS_LOCAL_LLM_ENABLED=1`` used to make telemetry report
    ``local:<model>`` even when no endpoint or model existed. Probe once at
    dispatcher build time and only inject the local rung when it is reachable.
    """
    if os.environ.get("JARVIS_LOCAL_LLM_ASSUME_AVAILABLE", "0") == "1":
        return True, "forced by JARVIS_LOCAL_LLM_ASSUME_AVAILABLE=1"

    url = base_url.rstrip("/") + "/models"
    probe_timeout = max(0.1, min(timeout_s, _local_probe_timeout_s()))
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {api_key}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=probe_timeout) as resp:
            body = resp.read(2_000_000)
    except urllib.error.URLError as e:
        return False, f"{url} unreachable: {e.reason}"
    except Exception as e:  # noqa: BLE001 - startup probe must never crash boot
        return False, f"{url} probe failed: {e}"

    try:
        payload = json.loads(body.decode("utf-8", errors="replace"))
    except Exception as e:  # noqa: BLE001
        return False, f"{url} returned non-JSON model list: {e}"

    ids: list[str] = []
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                mid = item.get("id") or item.get("name") or item.get("model")
            else:
                mid = item
            if mid:
                ids.append(str(mid))

    if not ids:
        return False, f"{url} returned no advertised models"
    if model not in ids:
        shown = ", ".join(ids[:8])
        suffix = "..." if len(ids) > 8 else ""
        return False, f"{model!r} not advertised by {url} ({shown}{suffix})"
    return True, f"{model!r} advertised by {url}"


def _local_probe_timeout_s() -> float:
    try:
        return float(os.environ.get("JARVIS_LOCAL_LLM_PROBE_TIMEOUT", "1.0"))
    except (TypeError, ValueError):
        return 1.0


def build_dispatching_llm(task_override: Optional[Any] = None) -> DispatchingLLM:
    """Construct route → inner-LLM mapping with Anthropic primaries
    (prompt-cached, ~700 ms TTFW warm) and Groq + DeepSeek as
    fast-fallback rungs.

    Per-route default chain (each entry is a FallbackAdapter):

        BANTER     → claude-haiku-4-5  → deepseek-v4-flash
        TASK       → claude-haiku-4-5  → deepseek-v4-flash
        REASONING  → claude-sonnet-4-6 → deepseek-v4-flash
        EMOTIONAL  → claude-haiku-4-5  → deepseek-v4-flash

    Anthropic is rung 1 because prompt caching gives ~700 ms TTFW on a
    warm cache. DeepSeek is rung 2 (cross-provider safety net): a single
    Anthropic 5xx / timeout cascades within 5 s (per-LLM `timeout=5.0`).
    The Groq legacy rung (rung 2 before) was removed 2026-06-29 in the
    full-Groq-eradication pass; DeepSeek took its slot.

    Per-route env overrides (operator tuning without code edits):
      JARVIS_BANTER_MODEL          (default claude-haiku-4-5)
      JARVIS_TASK_MODEL            (legacy; applies to all TASK_* sub-routes)
      JARVIS_TASK_DESKTOP_MODEL    (default claude-sonnet-4-6)
      JARVIS_TASK_BROWSER_MODEL    (default claude-sonnet-4-6)
      JARVIS_TASK_CODE_MODEL       (default deepseek-v4-flash)
      JARVIS_TASK_FILES_MODEL      (default claude-haiku-4-5)
      JARVIS_TASK_OTHER_MODEL      (default claude-haiku-4-5)
      JARVIS_REASONING_MODEL       (default claude-sonnet-4-6)
      JARVIS_EMOTIONAL_MODEL       (default claude-haiku-4-5)

    Per-sub-route env wins over the legacy JARVIS_TASK_MODEL when both
    are set. Spec defaults from pipeline.specialty_routes (the source of
    truth for the pre-TTS confab gate's per-route ladder).

    `task_override`: when not None, replaces the TASK route's inner LLM
    AND propagates across all TASK_* sub-routes (BANTER/REASONING/
    EMOTIONAL stay on their per-route defaults). Tray-pinned model wins
    over JARVIS_TASK_MODEL. Per global review §P0-12.

    Route map exposes 8 keys: BANTER, TASK_DESKTOP, TASK_BROWSER,
    TASK_CODE, TASK_FILES, TASK_OTHER, REASONING, EMOTIONAL. The legacy
    "TASK" key is still present (aliased to the same chain as task_inner)
    for code paths that haven't migrated to the 5-way TASK_* split.

    Graceful degrade: if `ANTHROPIC_API_KEY` is missing/empty (or the
    plugin isn't installed), the Anthropic primary construction is
    skipped per route and the shared DeepSeek instance comes UP as the
    rung-1 primary — dispatcher still boots, the user just loses the
    sub-second TTFW until a key is set. Same fallback when a specific
    Anthropic primary construction raises (e.g., upstream rejects the
    model id).
    """
    # Tight retry profile across all dispatcher LLMs. Default is
    # max_retries=3 which means up to 4 attempts × ~2 s backoff = ~10 s
    # of silence on a 4xx-but-classified-retryable error (e.g. tool-call
    # validation failure). 2026-05-02 13:20 incident: a desktop
    # subagent hung for ~2 minutes because its LLM cycled through
    # Groq → retry → DeepSeek → retry → Groq with the prior 8 s/req
    # timeout. Tightened to 5 s/req and 0 retries — single fail-over
    # is enough; the FallbackAdapter handles the cross-provider hop.
    # Worst case now: 5 s Anthropic + 5 s Groq + 5 s DeepSeek = 15 s
    # ceiling for a triple-blip, vs the ~120 s observed previously.
    LLM_KWARGS = {"max_retries": 0, "timeout": 5.0}

    anth_key = os.environ.get("ANTHROPIC_API_KEY", "")
    anth_armed = bool(_ANTHROPIC_AVAILABLE and anth_key)
    if not anth_armed:
        logger.warning(
            "[dispatch] ANTHROPIC_API_KEY missing or anthropic plugin unavailable — "
            "falling back to the DeepSeek primary per route (no prompt caching → ~2s TTFW)"
        )

    # Build a single shared DeepSeek instance; the FallbackAdapter chain
    # passes it as the LAST-tier provider on each route. Cross-provider
    # safety net (different network edge than Anthropic + Groq).
    ds_fallback = None
    ds_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if ds_key:
        try:
            # 2026-05-02: deepseek-v4-flash is ~30 % faster than v3 chat
            # AND has better tool-call accuracy (v4 family trained on
            # more agentic data). reasoning_content round-trip is
            # handled by deepseek_roundtrip.install() at the top of
            # jarvis_agent. Override via JARVIS_DS_FALLBACK_MODEL.
            ds_fallback = lk_openai.LLM(
                model=os.environ.get("JARVIS_DS_FALLBACK_MODEL", "deepseek-v4-flash"),
                api_key=ds_key,
                base_url="https://api.deepseek.com/v1",
                temperature=0.6,
                # Non-thinking: the fallback rung serves tool-forced routes,
                # and v4-flash's default thinking mode 400s on tool_choice=required.
                extra_body=_DEEPSEEK_NON_THINKING,
            )
            ds_fallback._jarvis_label = "deepseek:chat"
            logger.info("[dispatch] DeepSeek fallback armed (rung 3) for all routes")
        except Exception as e:
            logger.warning(f"[dispatch] DeepSeek fallback construction failed: {e}")
            ds_fallback = None
    else:
        logger.info("[dispatch] DEEPSEEK_API_KEY missing, no cross-provider fallback")

    # ── Rung-0: optional local / remote OpenAI-compatible LLM ──────────
    # When JARVIS_LOCAL_LLM_ENABLED=1 and the endpoint/model probe passes,
    # a local model (Ollama / vLLM / llama.cpp) is PREPENDED to every
    # in-scope route's FallbackAdapter as the first-tried rung — local
    # becomes primary while the existing Anthropic → Groq → DeepSeek chain
    # becomes the cloud fallback. `_strict_tool_schema=False` is MANDATORY
    # — every local server rejects/ignores OpenAI strict schema, which
    # silently breaks JARVIS's 20+ tools (livekit-plugins-openai defaults
    # it True; `with_ollama()` forgets to flip it — plugin bug).
    # URL-portable: 127.0.0.1 (this box) and a remote GPU server are
    # identical from JARVIS's view — just change JARVIS_LOCAL_LLM_URL.
    # Design: ~/.claude/plans/we-need-to-find-polymorphic-allen.md (2026-06-15).
    _local_enabled = os.environ.get("JARVIS_LOCAL_LLM_ENABLED", "0") == "1"
    _local_url     = os.environ.get(
        "JARVIS_LOCAL_LLM_URL", "http://127.0.0.1:11434/v1"
    ).strip() or "http://127.0.0.1:11434/v1"
    # `auto` → hardware-pick the best-fitting tool-capable Ollama tag
    # (VRAM/RAM scan); any explicit tag passes through unchanged.
    _local_model   = resolve_model_tag(
        os.environ.get("JARVIS_LOCAL_LLM_MODEL", "qwen3:14b").strip() or "qwen3:14b"
    )
    _local_api_key = os.environ.get("JARVIS_LOCAL_LLM_API_KEY", "ollama").strip() or "ollama"

    def _local_envf(name: str, default: float) -> float:
        try:
            raw = os.environ.get(name, "").strip()
            return float(raw) if raw else float(default)
        except (TypeError, ValueError):
            return float(default)

    _local_temp = _local_envf("JARVIS_LOCAL_LLM_TEMP", 0.6)
    # Generous default: a cold local-model load (or a big model on CPU)
    # can take far longer than the cloud rungs' 5s. A DOWN endpoint still
    # fails fast (APIConnectionError, not a timeout), so it cascades
    # immediately regardless of this value — the timeout only bounds the
    # reachable-but-slow case.
    _local_timeout = _local_envf("JARVIS_LOCAL_LLM_TIMEOUT", 60.0)
    _local_routes_raw = os.environ.get("JARVIS_LOCAL_LLM_ROUTES", "").strip()
    _local_routes = (
        {r.strip() for r in _local_routes_raw.split(",") if r.strip()}
        if _local_routes_raw else None
    )
    if _local_enabled:
        ok, reason = _probe_local_llm(
            _local_url,
            _local_model,
            _local_api_key,
            _local_timeout,
        )
        if ok:
            logger.info(
                "[dispatch] local LLM rung-0 ENABLED: model=%s url=%s routes=%s timeout=%.0fs (%s)",
                _local_model, _local_url,
                ",".join(sorted(_local_routes)) if _local_routes else "ALL",
                _local_timeout,
                reason,
            )
        else:
            logger.warning(
                "[dispatch] local LLM requested but unavailable: model=%s url=%s (%s); "
                "skipping local rung",
                _local_model,
                _local_url,
                reason,
            )
        _local_enabled = ok

    def _make_local_llm(route: str):
        """Build the rung-0 local OpenAI-compat LLM for `route`. Returns
        None when disabled/unavailable, the route is filtered out by
        JARVIS_LOCAL_LLM_ROUTES, or construction fails (route then starts at
        its cloud primary). `_strict_tool_schema=False` is MANDATORY (see
        block comment above)."""
        if not _local_enabled:
            return None
        if _local_routes is not None and route not in _local_routes:
            return None
        try:
            inst = lk_openai.LLM(
                model=_local_model,
                base_url=_local_url,
                api_key=_local_api_key,
                temperature=_local_temp,
                timeout=_local_timeout,
                max_retries=0,
                _strict_tool_schema=False,
            )
            inst._jarvis_label = f"local:{_local_model}"
            return inst
        except Exception as e:
            logger.warning(
                f"[dispatch] {route} local LLM rung-0 construction failed: {e} "
                "(route starts at cloud primary)"
            )
            return None

    def _build_gemini_primary(route: str, model_id: str, temp: float):
        """Build a Gemini primary at rung 1 for `route`. Used when the
        per-route env var resolves to ``gemini-*`` instead of an
        Anthropic model id. Returns None when the Google plugin isn't
        importable, the API key is unset, or construction raises
        (route then falls back to its Groq legacy)."""
        if not os.environ.get("GOOGLE_API_KEY", "").strip():
            logger.info(
                f"[dispatch] {route} requested Gemini {model_id!r} but "
                "GOOGLE_API_KEY is unset; degrading to the route fallback"
            )
            return None
        try:
            # Late import so a missing livekit-plugins-google doesn't
            # break the dispatcher build at all — only the Gemini-routed
            # route degrades.
            from providers.gemini_llm import GeminiCachedLLM
            inst = GeminiCachedLLM(
                model=model_id,
                api_key=os.environ.get("GOOGLE_API_KEY", ""),
                temperature=temp,
                max_output_tokens=200,
            )
            inst._jarvis_label = f"gemini:{model_id}"
            return inst
        except Exception as e:
            logger.warning(
                f"[dispatch] {route} Gemini primary {model_id!r} construction failed: {e} "
                "(degrading to the route fallback)"
            )
            return None

    # Legacy JARVIS_TASK_MODEL still works — when set, it applies to ALL
    # TASK_* sub-routes (tray-pinned model wins over per-sub-route
    # default). Per-sub-route env var (JARVIS_TASK_DESKTOP_MODEL etc.)
    # still wins over the legacy when both are set. Added 2026-05-24
    # alongside the pre-TTS confab gate's 4→8 route expansion.
    _legacy_task = os.environ.get("JARVIS_TASK_MODEL", "").strip() or None

    def _resolve_route_model(route: str) -> tuple[str, float]:
        """Resolve a single route's primary model id + temperature.

        Lookup order:
          1. Per-sub-route env var (JARVIS_TASK_DESKTOP_MODEL etc.) wins.
          2. For TASK_* routes, legacy JARVIS_TASK_MODEL applies.
          3. Spec default from specialty_routes (or _ANTH_DEFAULT_PER_ROUTE).
        Temperature comes from _ANTH_DEFAULT_PER_ROUTE (per-route tuned)."""
        env_var, default_model, temp = _ANTH_DEFAULT_PER_ROUTE[route]
        override = os.environ.get(env_var, "").strip()
        if override:
            return override, temp
        if _legacy_task and route.startswith("TASK_"):
            return _legacy_task, temp
        # Cross-check against specialty_routes for the 8-route table.
        # get_primary_model honors the per-sub-route env (which we
        # already checked above) and otherwise returns the spec default.
        spec_default = _specialty.get_primary_model(route)
        return (spec_default or default_model), temp

    def _build_anthropic_primary(route: str):
        """Build the route's primary LLM (rung 1). Honors the per-route
        env override AND the legacy JARVIS_TASK_MODEL propagation for
        TASK_* sub-routes. Returns None when the resolved provider isn't
        armed (no API key / plugin missing) or construction raises
        (route falls back to its Groq legacy).

        Picks a builder by model-id prefix:
          - ``gemini-*``    → Gemini builder
          - ``deepseek-*``  → DeepSeek inline OpenAI-compat builder
          - otherwise      → Anthropic cached LLM
        """
        model, temp = _resolve_route_model(route)
        # Operator opted into Gemini via JARVIS_{route}_MODEL=gemini-*.
        # Route through the Gemini builder regardless of whether
        # ANTHROPIC_API_KEY is also present.
        if model.startswith("gemini-"):
            return _build_gemini_primary(route, model, temp)
        # 2026-05-24: TASK_CODE's spec primary is deepseek-v4-flash, so
        # route deepseek-* ids through the OpenAI-compat DeepSeek builder
        # — Anthropic would 400 on a non-Anthropic model id at request
        # time, which the FallbackAdapter would mask but at cost of TTFW.
        if model.startswith("deepseek-"):
            if not ds_key:
                logger.info(
                    f"[dispatch] {route} requested DeepSeek {model!r} but "
                    "DEEPSEEK_API_KEY is unset; degrading to the route fallback"
                )
                return None
            try:
                tc = _tool_choice_for_route(route)
                inst_kwargs = {
                    "model": model,
                    "api_key": ds_key,
                    "base_url": "https://api.deepseek.com/v1",
                    "temperature": temp,
                }
                if tc is not None:
                    inst_kwargs["tool_choice"] = tc
                inst = lk_openai.LLM(**inst_kwargs)
                inst._jarvis_label = f"deepseek:{model}"
                return inst
            except Exception as e:
                logger.warning(
                    f"[dispatch] {route} DeepSeek primary {model!r} construction failed: {e} "
                    "(degrading to the route fallback)"
                )
                return None
        if not anth_armed:
            return None
        try:
            # Cache wiring (2026-05-23 refactor): build the
            # `AnthropicCachedLLM` wrapper so cache_control lands on the
            # STABLE prefix instead of the volatile tail. Real-world
            # claude-haiku-4-5 hit rate measured on 172 turns climbed
            # from 81 % → ~95 %+ once memory writes + breaker flips
            # stopped invalidating the cache (the volatile suffix now
            # sits past the breakpoint). The wrapper still hands ~700 ms
            # TTFW on warm hits but the hit rate is the load-bearing
            # win. We don't pass `caching="ephemeral"` — the subclass
            # owns cache_control placement (see its module docstring).
            from providers.anthropic_cached_llm import AnthropicCachedLLM
            tc = _tool_choice_for_route(route)
            ack = {
                "model": model,
                "api_key": anth_key,
                "temperature": temp,
                "max_tokens": 200,
                # See SPEECH_MODELS entry for full rationale. tl;dr:
                # defense-in-depth — the real fix for the 400
                # additionalProperties=false rejection is the
                # anthropic_strict_schema sanitizer in jarvis_agent.py.
                "_strict_tool_schema": False,
            }
            if tc is not None:
                ack["tool_choice"] = tc
            inst = AnthropicCachedLLM(**ack)
            inst._jarvis_label = f"anthropic:{model}"
            return inst
        except Exception as e:
            logger.warning(
                f"[dispatch] {route} Anthropic primary {model!r} construction failed: {e}"
            )
            return None

    def _wrap_chain(route: str, primary):
        """Wrap a route's primary LLM in a FallbackAdapter chain. When
        JARVIS_LOCAL_LLM_ENABLED=1 and the startup probe passes, a local
        LLM is prepended as rung 0 (tried first); the route's Groq legacy
        is rung 2 and DeepSeek rung 3 (when each is available). Labels the
        chain by its FIRST rung for telemetry. Returns the primary
        unwrapped when no other rungs are available."""
        rungs: list[Any] = [primary]
        primary_label = getattr(primary, "_jarvis_label", "")
        # Rung 2: shared DeepSeek (cross-provider safety net). Skip when
        # the primary IS already DeepSeek (e.g. TASK_CODE's default
        # deepseek-v4-flash) — otherwise rungs 1 and 2 are the same
        # model/endpoint/key, so a DeepSeek outage kills both rungs and
        # the chain is effectively "DeepSeek → (dead)".
        if ds_fallback is not None and not primary_label.startswith("deepseek:"):
            rungs.append(ds_fallback)
        # Rung 0: prepend the local LLM so it is TRIED FIRST (local-primary).
        # Unreachable/slow → FallbackAdapter cascades to `primary` (cloud).
        # Gated, probed, and route-filtered inside _make_local_llm, so this
        # is a no-op unless a usable local endpoint/model was found at boot.
        local_rung = _make_local_llm(route)
        if local_rung is not None:
            rungs.insert(0, local_rung)
        if len(rungs) == 1:
            return primary
        try:
            from livekit.agents.llm import FallbackAdapter as _LLMFallback
            wrapped = _LLMFallback(rungs)
            # Label the chain by its FIRST rung (what's actually tried
            # first): `local:<model>` when rung-0 is active, else the
            # cloud primary's label — behavior-preserving when local is
            # off (rungs[0] IS primary). dispatching_llm reads this for
            # the telemetry `model` column.
            wrapped._jarvis_label = (
                getattr(rungs[0], "_jarvis_label", "") or primary_label or "?"
            )
            return wrapped
        except Exception as e:
            logger.warning(
                f"[dispatch] {route} LLM FallbackAdapter wrap failed ({e}); "
                "using primary alone"
            )
            return primary

    def _build_route(route: str):
        """Build the full FallbackAdapter chain for `route`. Tries
        Anthropic primary first; falls back to the route's Groq legacy
        as the rung-1 primary if Anthropic is unavailable. Logs which
        primary actually landed at rung 1 for operator visibility."""
        primary = _build_anthropic_primary(route)
        if primary is None:
            # No Anthropic primary (missing key / plugin / construction
            # error) → degrade to the shared DeepSeek instance as the
            # rung-1 primary so the dispatcher still boots. (Was the Groq
            # legacy rung before the 2026-06-29 full-Groq removal.)
            primary = ds_fallback
        if primary is None:
            # No cloud primary built (missing keys / construction error).
            # If a local rung is enabled for this route, the local model
            # BECOMES the primary so a true offline / cloud-keyless boot
            # still works — this is the plan's "stay alive when ALL cloud
            # is unavailable" path. Returned bare (not via _wrap_chain,
            # which would re-inject local and double it); there's no cloud
            # rung left to fall back to anyway.
            local_only = _make_local_llm(route)
            if local_only is not None:
                logger.info(
                    f"[dispatch] {route} primary: {local_only._jarvis_label} "
                    "(local-only; no cloud primary available)"
                )
                return local_only
            # Both cloud providers AND local failed/disabled. Caller will
            # substitute the TASK route's chain (which is also the
            # dispatcher fallback) — see the post-loop assembly below.
            logger.error(
                f"[dispatch] {route} primary construction failed entirely "
                "(no Anthropic, no DeepSeek, no local); route will inherit TASK fallback"
            )
            return None
        primary_label = getattr(primary, "_jarvis_label", "?")
        cached_suffix = " (cached)" if primary_label.startswith("anthropic:") else ""
        logger.info(f"[dispatch] {route} primary: {primary_label}{cached_suffix}")
        return _wrap_chain(route, primary)

    banter       = _build_route("BANTER")
    task_main    = _build_route("TASK")
    reasoning    = _build_route("REASONING")
    emotional    = _build_route("EMOTIONAL")
    task_desktop = _build_route("TASK_DESKTOP")
    task_browser = _build_route("TASK_BROWSER")
    task_code    = _build_route("TASK_CODE")
    task_files   = _build_route("TASK_FILES")
    task_other   = _build_route("TASK_OTHER")

    # Any route that failed primary construction entirely inherits the
    # TASK chain. If TASK itself failed (rare — both Anthropic AND
    # Groq construction blew up), we still need *something*; pick the
    # first non-None route or, as a last resort, the bare DeepSeek
    # fallback. Refusing to boot is worse than booting with a single
    # provider — the user can't even hear an error otherwise.
    main = task_main
    if main is None:
        main = next(
            (r for r in (banter, reasoning, emotional) if r is not None),
            ds_fallback,
        )
    if banter is None:
        banter = main
    if reasoning is None:
        reasoning = main
    if emotional is None:
        emotional = main
    # TASK_* sub-routes inherit the TASK chain if their own primary
    # failed to build (e.g., DEEPSEEK_API_KEY unset for TASK_CODE).
    if task_desktop is None:
        task_desktop = main
    if task_browser is None:
        task_browser = main
    if task_code is None:
        task_code = main
    if task_files is None:
        task_files = main
    if task_other is None:
        task_other = main

    # task_override takes precedence over the env-driven TASK defaults
    # AND propagates across all TASK_* sub-routes — tray-pinned model
    # wins everywhere it could land. Per global review §P0-12 plus the
    # 2026-05-24 8-route expansion.
    if task_override is not None:
        task_inner   = task_override
        task_desktop = task_override
        task_browser = task_override
        task_code    = task_override
        task_files   = task_override
        task_other   = task_override
    else:
        task_inner = main

    # Log the resolved per-route model ids for operator visibility.
    # Each `_resolve_route_model(route)[0]` returns the id that was
    # actually selected (after env + legacy + spec-default lookup).
    banter_id       = _resolve_route_model("BANTER")[0]
    task_desktop_id = _resolve_route_model("TASK_DESKTOP")[0]
    task_browser_id = _resolve_route_model("TASK_BROWSER")[0]
    task_code_id    = _resolve_route_model("TASK_CODE")[0]
    task_files_id   = _resolve_route_model("TASK_FILES")[0]
    task_other_id   = _resolve_route_model("TASK_OTHER")[0]
    reasoning_id    = _resolve_route_model("REASONING")[0]
    emotional_id    = _resolve_route_model("EMOTIONAL")[0]
    logger.info(
        f"[dispatch] LLM dispatcher resolved: "
        f"BANTER={banter_id}, "
        f"TASK_DESKTOP={task_desktop_id}, TASK_BROWSER={task_browser_id}, "
        f"TASK_CODE={task_code_id}, TASK_FILES={task_files_id}, TASK_OTHER={task_other_id}, "
        f"REASONING={reasoning_id}, EMOTIONAL={emotional_id}"
    )

    return DispatchingLLM(
        inners={
            "BANTER":       banter,
            "TASK":         task_inner,
            "TASK_DESKTOP": task_desktop,
            "TASK_BROWSER": task_browser,
            "TASK_CODE":    task_code,
            "TASK_FILES":   task_files,
            "TASK_OTHER":   task_other,
            "REASONING":    reasoning,
            "EMOTIONAL":    emotional,
        },
        fallback=task_inner,
    )
