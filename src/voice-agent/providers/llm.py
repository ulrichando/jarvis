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
import logging
import os
from pathlib import Path

from livekit.agents import APIConnectionError, APITimeoutError
from livekit.plugins import groq, openai as lk_openai

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

from pipeline.dispatching_llm import DispatchingLLM
from pipeline.settings import read_unified_setting
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
    "BreakeredGroqLLM",
    # Per-route dispatcher build
    "build_dispatching_llm",
]


# ── User-selected speech LLM (tray pick) ─────────────────────────────
# The tray UI writes the active id to ~/.jarvis/voice-model (or to the
# hub's state.db). entrypoint() calls `make_speech_llm()` once per job
# so a /voice-model POST + systemctl restart picks up the new file on
# the very next dispatch.
SPEECH_MODEL_FILE: Path = Path.home() / ".jarvis" / "voice-model"
DEFAULT_SPEECH_MODEL: str = "gpt-5-mini"

# IDs match the upstream model names verbatim so the registry stays
# legible. Each entry: (provider+model labels for display, factory
# building the LLM). Factories raise on missing API key — the
# read_speech_model() helper falls back to the default if so.
SPEECH_MODELS: dict[str, dict] = {
    "llama-3.3-70b-versatile": {
        "label": "Groq · llama 3.3 70B",
        "build": lambda: groq.LLM(model="llama-3.3-70b-versatile", temperature=0.6),
    },
    "llama-3.1-8b-instant": {
        # Tiny + fastest. Function calling is acceptable for simple
        # tool routing but loses nuance on long multi-step replies.
        "label": "Groq · llama 3.1 8B instant",
        "build": lambda: groq.LLM(model="llama-3.1-8b-instant", temperature=0.6),
    },
    "qwen/qwen3-32b": {
        # Strong tool calling, slightly slower than llama 3.3 70b but
        # markedly more reliable at structured function calls.
        "label": "Groq · qwen3-32b",
        "build": lambda: groq.LLM(model="qwen/qwen3-32b", temperature=0.6),
    },
    "openai/gpt-oss-120b": {
        # Same model the CLI tool uses by default. Robust at tool
        # calls; somewhat slower first token (~400 ms).
        "label": "Groq · gpt-oss-120b",
        "build": lambda: groq.LLM(model="openai/gpt-oss-120b", temperature=0.6),
    },
    "meta-llama/llama-4-scout-17b-16e-instruct": {
        "label": "Groq · llama 4 scout",
        "build": lambda: groq.LLM(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            temperature=0.6,
        ),
    },
    # DeepSeek family — needs reasoning_content round-trip on
    # assistant tool-call messages, handled by deepseek_roundtrip.install()
    # at the top of jarvis_agent. v4-pro is best at tools; v4-flash trades
    # accuracy for ~30% latency reduction; deepseek-chat (V3) is the
    # non-thinking baseline (probe shows it never emits
    # reasoning_content even with the flag absent, so the patch's
    # capture path is dead for it).
    "deepseek-chat": {
        "label": "DeepSeek · chat (V3, non-thinking)",
        "build": lambda: lk_openai.LLM(
            model="deepseek-chat",
            api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
            base_url="https://api.deepseek.com/v1",
            temperature=0.6,
        ),
    },
    "deepseek-v4-flash": {
        "label": "DeepSeek · v4 flash",
        "build": lambda: lk_openai.LLM(
            model="deepseek-v4-flash",
            api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
            base_url="https://api.deepseek.com/v1",
            temperature=0.6,
        ),
    },
    "deepseek-v4-pro": {
        "label": "DeepSeek · v4 pro",
        "build": lambda: lk_openai.LLM(
            model="deepseek-v4-pro",
            api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
            base_url="https://api.deepseek.com/v1",
            temperature=0.6,
        ),
    },
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
    #   gpt-5-nano       → fastest + cheapest, weakest tool calling
    #   gpt-5-mini       → voice sweet spot (~300-500 ms first token)
    #   gpt-5            → base tier, ~50 % slower than mini but smarter
    #   gpt-5.1          → latest generation, best general quality
    #   gpt-5.1-chat-latest → pinned chat variant (auto-rolls)
    #   gpt-5-pro        → most capable, materially slower (reserve
    #                       for multi-step delegations where accuracy
    #                       outweighs latency)
    #   gpt-5-codex      → code-specialized; route here for coding turns
    #   gpt-4o           → legacy classic, supports temperature, fast
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
    SPEECH_MODELS["gpt-5-pro"] = {
        "label": "OpenAI · GPT-5 pro (most capable, slowest)",
        "build": lambda: lk_openai.LLM(model="gpt-5-pro"),
    }
    SPEECH_MODELS["gpt-5-codex"] = {
        "label": "OpenAI · GPT-5 codex (code-specialized)",
        "build": lambda: lk_openai.LLM(model="gpt-5-codex"),
    }
    # gpt-4o still accepts temperature, so we keep the matching 0.6
    # used across the other speech models.
    SPEECH_MODELS["gpt-4o"] = {
        "label": "OpenAI · GPT-4o (classic, supports temperature)",
        "build": lambda: lk_openai.LLM(model="gpt-4o", temperature=0.6),
    }

if _ANTHROPIC_AVAILABLE and os.environ.get("ANTHROPIC_API_KEY", ""):
    # Shared kwargs across Anthropic tiers — same `_strict_tool_schema`,
    # `caching`, and `max_tokens` discipline applies to every Claude
    # speech model. Pulled into a helper so adding a tier is a single
    # `model=` line.
    def _make_anthropic_speech_llm(model_id: str):
        return lk_anthropic.LLM(
            model=model_id,
            api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
            temperature=0.6,
            max_tokens=200,
            caching="ephemeral",
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


def read_speech_model() -> str:
    """Return the active speech model ID, or the default if unset/invalid.

    Reads via the unified-settings SDK (state.db) first, falling back
    to the flat file written by the tray UI."""
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


# ── BreakeredGroqLLM ─────────────────────────────────────────────────

class BreakeredGroqLLM(groq.LLM):
    """`groq.LLM` whose `chat()` returns a stream gated by `LLM_BREAKER`.
    The first chunk read goes through the breaker; later chunks pass
    through unmodified. When the breaker is open or the breaker's own
    timeout fires, the FallbackAdapter sees `APIConnectionError` /
    `APITimeoutError` and cascades to the next LLM (typically DeepSeek)
    within ms instead of the upstream's ~30 s default.

    Also runs a pre-flight token-estimation pass per turn (port from
    claude-code's services/tokenEstimation.ts) and stashes the result
    on `LAST_PREFLIGHT` so the per-turn telemetry write can pick it up.
    Pressure-state at WARN/HARD logs a `[token-estimation]` line so
    the operator sees context filling up before Groq returns 413.
    """

    def chat(self, *args, **kw):
        # Pre-flight token estimation. Best-effort; never raises.
        try:
            from tools.token_estimation import (
                estimate_tokens,
                context_pressure_state,
                MAX_CONTEXT_TOKENS,
            )
            chat_ctx = kw.get("chat_ctx")
            tools = kw.get("tools") or []
            # Cheap stringification — duck-typed across LiveKit
            # ChatContext / FunctionTool versions. The exact byte
            # count differs from upstream tokenization but is
            # consistent per-process so threshold tracking works.
            ctx_str = ""
            try:
                items = getattr(chat_ctx, "items", None) or []
                for it in items:
                    ctx_str += str(getattr(it, "content", it)) + "\n"
            except Exception:
                ctx_str = str(chat_ctx) if chat_ctx is not None else ""
            tools_str = ""
            try:
                for t in tools:
                    info = getattr(t, "info", None)
                    if info is not None:
                        tools_str += (
                            (getattr(info, "name", "") or "")
                            + " "
                            + (getattr(info, "description", "") or "")
                            + "\n"
                        )
                    else:
                        tools_str += str(t) + "\n"
            except Exception:
                pass
            est = estimate_tokens(ctx_str) + estimate_tokens(tools_str)
            pressure = context_pressure_state(est)
            label = getattr(self, "_jarvis_label", "?")
            # Stash for the per-turn telemetry write to read. Plain
            # dict update (not ContextVar) — see the LAST_PREFLIGHT
            # comment above for why.
            LAST_PREFLIGHT["tokens"] = est
            LAST_PREFLIGHT["pressure"] = pressure
            LAST_PREFLIGHT["model"] = label
            if pressure != "ok":
                logger.warning(
                    f"[token-estimation] {label} pressure={pressure} "
                    f"est_tokens={est} max={MAX_CONTEXT_TOKENS}"
                )
            # Token-aware hard prune (added 2026-05-08, fix B in the
            # voice-channel audit). Live-captured pre-flight at 17:51
            # showed est_tokens=293321 against max=128000 and the
            # supervisor LLM degenerated into "delegate to summarize"
            # for every utterance because Groq silently truncated the
            # JARVIS_INSTRUCTIONS preamble.
            #
            # Approach: when the estimate exceeds a safe budget (target
            # leaves ~13K headroom for response output + tool overhead),
            # build a pruned ChatContext by dropping oldest non-system
            # items until the estimate fits. Replace kw["chat_ctx"]
            # only for THIS call — the AgentSession keeps the full
            # history; we just send less to the LLM.
            if (
                pressure == "hard"
                and chat_ctx is not None
                and os.environ.get("JARVIS_TOKEN_AWARE_PRUNE", "1") == "1"
            ):
                # Target leaves headroom for tools (already counted) +
                # ~8K for response output. Anything over WARN_TOKENS
                # post-prune still fires the warning above so the
                # operator knows pruning was active.
                target = max(40_000, MAX_CONTEXT_TOKENS - 13_000) - estimate_tokens(tools_str)
                pruned = prune_chat_ctx_for_budget(chat_ctx, target)
                pruned_items = getattr(pruned, "items", None) or []
                original_items = getattr(chat_ctx, "items", None) or []
                if len(pruned_items) < len(original_items):
                    dropped = len(original_items) - len(pruned_items)
                    new_est = ctx_items_token_estimate(pruned_items) + estimate_tokens(tools_str)
                    logger.warning(
                        f"[token-prune] dropped {dropped} oldest non-system "
                        f"items: {len(original_items)}→{len(pruned_items)} "
                        f"items, est {est}→{new_est} tokens"
                    )
                    kw["chat_ctx"] = pruned
                    LAST_PREFLIGHT["tokens"] = new_est
                    LAST_PREFLIGHT["pressure"] = context_pressure_state(new_est)
        except Exception:
            # Pre-flight is purely diagnostic — never block the call.
            pass
        inner_stream = super().chat(*args, **kw)
        return BreakeredLLMStream(inner_stream, LLM_BREAKER)

    @staticmethod
    async def _call_with_breaker_for_test():
        """Test seam — exercises only the breaker-open path with a
        no-op coroutine. Cheap to invoke and proves the breaker
        conversion (`CircuitOpenError` → `APIConnectionError`,
        `asyncio.TimeoutError` → `APITimeoutError`) works in
        isolation. Like the TTS seam (Task 3), the LLM factory is
        straightforward enough that we don't need the seam itself to
        drive construction.

        Limitation: this seam does NOT exercise the full caller
        contract (e.g. `async with stream: async for chunk in stream:`
        used by livekit-agents). Tests that need to verify the wrapper
        honours protocol methods must construct the wrapper class
        directly and drive it through async with + async for — see
        test_breaker_llm_open_raises_apiconnection_error for the
        pattern."""
        async def _no_op():
            return None
        try:
            return await LLM_BREAKER.call(_no_op)
        except CircuitOpenError as e:
            raise APIConnectionError() from e
        except asyncio.TimeoutError:
            raise APITimeoutError() from None


# ── Per-route DispatchingLLM build ───────────────────────────────────

def build_dispatching_llm() -> DispatchingLLM:
    """Construct route → inner-LLM mapping using Groq variants, each
    wrapped in a FallbackAdapter([groq, deepseek-v4]) so a Groq-edge
    connection blip falls through to DeepSeek instead of losing the
    turn.

    BANTER     → llama-3.1-8b-instant (fastest)
    TASK       → llama-3.3-70b-versatile (current default, tools)
    REASONING  → qwen/qwen3-32b (structured reasoning)
    EMOTIONAL  → llama-4-scout (warmer temperament, temp 0.7)

    DeepSeek-v4-flash is the per-route safety net since it has a
    different network edge than Groq. Phase 10.2 sanitizer + Phase
    10.3 deepseek_roundtrip patches still apply transparently.
    """
    # Tight retry profile across all dispatcher LLMs. Default is
    # max_retries=3 which means up to 4 attempts × ~2 s backoff = ~10 s
    # of silence on a 4xx-but-classified-retryable error (e.g. tool-call
    # validation failure). 2026-05-02 13:20 incident: a desktop
    # subagent hung for ~2 minutes because its LLM cycled through
    # Groq → retry → DeepSeek → retry → Groq with the prior 8 s/req
    # timeout. Tightened to 5 s/req and 0 retries — single fail-over
    # is enough; the FallbackAdapter handles the cross-provider hop.
    # Worst case now: 5s Groq + 5s DeepSeek = 10s ceiling, vs the
    # ~120s observed previously.
    LLM_KWARGS = {"max_retries": 0, "timeout": 5.0}

    # Build a single shared DeepSeek instance; the FallbackAdapter chain
    # passes it as the second-tier provider on each route.
    ds_fallback = None
    ds_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if ds_key:
        try:
            # 2026-05-02: switched fallback from deepseek-chat (V3,
            # non-thinking) to deepseek-v4-flash. Rationale: Groq has
            # been throwing "Failed to call a function" frequently, so
            # the fallback fires often. V4-flash is ~30% faster than
            # V3 chat AND has better tool-call accuracy (V4 family
            # was trained on more agentic data). reasoning_content
            # round-trip is handled by deepseek_roundtrip.install()
            # at the top of jarvis_agent. Override via env if you want
            # a different fallback model.
            ds_fallback = lk_openai.LLM(
                model=os.environ.get("JARVIS_DS_FALLBACK_MODEL", "deepseek-v4-flash"),
                api_key=ds_key,
                base_url="https://api.deepseek.com/v1",
                temperature=0.6,
            )
            ds_fallback._jarvis_label = "deepseek:chat"
            logger.info("[dispatch] DeepSeek fallback armed for all routes")
        except Exception as e:
            logger.warning(f"[dispatch] DeepSeek fallback construction failed: {e}")
            ds_fallback = None
    else:
        logger.info("[dispatch] DEEPSEEK_API_KEY missing, no cross-provider fallback")

    # Third fallback rung: Anthropic Claude Sonnet 4.6. Only ever fires
    # if both Groq (primary) AND DeepSeek (rung 2) fail back-to-back —
    # historically rare (4/142 sessions on this host). Paid per-token,
    # so we keep it disabled when no ANTHROPIC_API_KEY is present.
    # Sonnet (not Haiku) on the fallback rung: when both Groq and
    # DeepSeek are blipping the turn is already degraded, so a smarter
    # model is worth the extra ~300ms + ~3x cost — the alternative is
    # the user hearing nothing. Override via JARVIS_ANTHROPIC_FALLBACK_MODEL
    # if cost becomes a concern.
    anth_fallback = None
    anth_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if _ANTHROPIC_AVAILABLE and anth_key:
        try:
            anth_model = os.environ.get(
                "JARVIS_ANTHROPIC_FALLBACK_MODEL", "claude-sonnet-4-6"
            )
            anth_fallback = lk_anthropic.LLM(
                model=anth_model,
                api_key=anth_key,
                temperature=0.6,
                max_tokens=200,
                caching="ephemeral",
                # See the SPEECH_MODELS entry above for the full
                # rationale. tl;dr: defense-in-depth only — the real
                # fix for the additionalProperties=false rejection
                # is the anthropic_strict_schema sanitizer in
                # jarvis_agent.py. max_tokens=200 caps response
                # verbosity same as the speech-model path.
                _strict_tool_schema=False,
            )
            anth_fallback._jarvis_label = f"anthropic:{anth_model}"
            logger.info(f"[dispatch] Anthropic {anth_model} fallback armed (rung 3)")
        except Exception as e:
            logger.warning(f"[dispatch] Anthropic fallback construction failed: {e}")
            anth_fallback = None

    def _wrap(primary):
        """Wrap a Groq LLM in FallbackAdapter([groq, deepseek, anthropic])
        so successive provider blips transparently route through to the
        next rung. Preserves _jarvis_label for telemetry."""
        rungs = [primary]
        if ds_fallback is not None:
            rungs.append(ds_fallback)
        if anth_fallback is not None:
            rungs.append(anth_fallback)
        if len(rungs) == 1:
            return primary
        try:
            from livekit.agents.llm import FallbackAdapter as _LLMFallback
            wrapped = _LLMFallback(rungs)
            wrapped._jarvis_label = getattr(primary, "_jarvis_label", "?")
            return wrapped
        except Exception as e:
            logger.warning(f"[dispatch] LLM FallbackAdapter wrap failed ({e}); using primary alone")
            return primary

    # NOTE 2026-05-02: prompt_cache_key was added on commit 892e5e7
    # for latency, then REVERTED on commit-after-this — Groq's API
    # returns HTTP 400 'property prompt_cache_key is unsupported' on
    # every call that includes it. The parameter exists on the
    # livekit-plugins-openai client (OpenAI proper supports it) but
    # Groq's compatibility layer rejects it. Don't re-add until
    # Groq announces support. Latency improvement still pending —
    # next try should be Groq's `service_tier` field instead.
    main_raw = BreakeredGroqLLM(
        model="llama-3.3-70b-versatile", temperature=0.6, **LLM_KWARGS,
    )
    main_raw._jarvis_label = "groq:llama-3.3-70b-versatile"
    main = _wrap(main_raw)

    try:
        banter_raw = BreakeredGroqLLM(
            model="llama-3.1-8b-instant", temperature=0.6, **LLM_KWARGS,
        )
        banter_raw._jarvis_label = "groq:llama-3.1-8b-instant"
        banter = _wrap(banter_raw)
    except Exception as e:
        logger.warning(f"[dispatch] BANTER LLM construction failed: {e}; using main")
        banter = main

    try:
        reasoning_raw = BreakeredGroqLLM(
            model="qwen/qwen3-32b", temperature=0.6, **LLM_KWARGS,
        )
        reasoning_raw._jarvis_label = "groq:qwen3-32b"
        reasoning = _wrap(reasoning_raw)
    except Exception as e:
        logger.warning(f"[dispatch] REASONING LLM construction failed: {e}; using main")
        reasoning = main

    try:
        emotional_raw = BreakeredGroqLLM(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            temperature=0.7, **LLM_KWARGS,
        )
        emotional_raw._jarvis_label = "groq:llama-4-scout"
        emotional = _wrap(emotional_raw)
    except Exception as e:
        logger.warning(f"[dispatch] EMOTIONAL LLM construction failed: {e}; using main")
        emotional = main

    return DispatchingLLM(
        inners={
            "BANTER":    banter,
            "TASK":      main,
            "REASONING": reasoning,
            "EMOTIONAL": emotional,
        },
        fallback=main,
    )
