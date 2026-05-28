"""Specialty routes dispatch — per-route model + retry-ladder table.

Spec: docs/superpowers/specs/2026-05-24-pre-tts-confab-gate-design.md §
"Model assignment per sub-route"

This module is PURE data + lookups. It returns model IDs (strings)
keyed by route + tier. The provider construction (LLM instances,
FallbackAdapter chains) happens in providers/llm.py — which consults
this module to pick the right model for each route.

Each route has 4 tiers:
  tier 0 — primary model (the default for that route)
  tier 1 — same model + tool-forcing system message (retry path)
  tier 2 — escalation to a more capable model
  tier 3 — cross-provider safety net

For BANTER and EMOTIONAL, only tier 0 is defined — those routes
never go through the confab retry chain.

Env overrides (operator tuning without code edits):
  JARVIS_TASK_DESKTOP_MODEL   (default claude-sonnet-4-6)
  JARVIS_TASK_BROWSER_MODEL   (default claude-sonnet-4-6)
  JARVIS_TASK_CODE_MODEL      (default deepseek-v4-flash)
  JARVIS_TASK_FILES_MODEL     (default claude-haiku-4-5)
  JARVIS_TASK_OTHER_MODEL     (default claude-sonnet-4-6)
  JARVIS_BANTER_MODEL         (default claude-haiku-4-5; existing)
  JARVIS_REASONING_MODEL      (default claude-sonnet-4-6; existing)
  JARVIS_EMOTIONAL_MODEL      (default claude-haiku-4-5; existing)

The Kimi K2.6 entry for TASK_BROWSER tier-2 is suppressed unless
JARVIS_KIMI_VOICE_EXPERIMENTAL=1 (the K2.6 voice supervisor is
currently broken — 'web_search not in request.tools'). When
suppressed, the slot falls through to claude-opus-4-7.
"""
from __future__ import annotations

import os
from typing import Optional

# Tier labels for clarity in callers.
TIER_PRIMARY        = "primary"
TIER_RETRY          = "retry"
TIER_ESCALATE       = "escalate"
TIER_CROSS_PROVIDER = "cross_provider"
TIERS = (TIER_PRIMARY, TIER_RETRY, TIER_ESCALATE, TIER_CROSS_PROVIDER)

# Default ladder per route. The retry tier (tier 1) is conceptually the
# same model as the primary — the difference is the tool-forcing system
# message appended for that call. We model it as "same string" here and
# let the gate orchestration know to use the tool-force prompt on retry.
_DEFAULTS: dict[str, dict[str, Optional[str]]] = {
    "TASK_DESKTOP": {
        TIER_PRIMARY:        "claude-sonnet-4-6",
        TIER_RETRY:          "claude-sonnet-4-6",  # same model + force prompt
        TIER_ESCALATE:       "claude-opus-4-7",
        TIER_CROSS_PROVIDER: "gpt-5.1",
    },
    "TASK_BROWSER": {
        TIER_PRIMARY:        "claude-sonnet-4-6",
        TIER_RETRY:          "claude-sonnet-4-6",
        # Kimi K2.6 swaps in here when JARVIS_KIMI_VOICE_EXPERIMENTAL=1 —
        # handled via lookup-time env check in get_route_ladder().
        TIER_ESCALATE:       "claude-opus-4-7",
        TIER_CROSS_PROVIDER: "gpt-5.1",
    },
    "TASK_CODE": {
        TIER_PRIMARY:        "deepseek-v4-flash",
        TIER_RETRY:          "deepseek-v4-flash",
        TIER_ESCALATE:       "claude-sonnet-4-6",
        TIER_CROSS_PROVIDER: "gpt-5.1",
    },
    "TASK_FILES": {
        TIER_PRIMARY:        "claude-haiku-4-5",
        TIER_RETRY:          "claude-haiku-4-5",
        TIER_ESCALATE:       "claude-sonnet-4-6",
        TIER_CROSS_PROVIDER: "deepseek-v4-flash",
    },
    "TASK_OTHER": {
        TIER_PRIMARY:        "claude-sonnet-4-6",
        TIER_RETRY:          "claude-sonnet-4-6",
        TIER_ESCALATE:       "claude-opus-4-7",
        TIER_CROSS_PROVIDER: "gpt-5-mini",
    },
    "BANTER": {
        TIER_PRIMARY:        "claude-haiku-4-5",
        TIER_RETRY:          None,
        TIER_ESCALATE:       None,
        TIER_CROSS_PROVIDER: None,
    },
    "REASONING": {
        TIER_PRIMARY:        "claude-sonnet-4-6",
        TIER_RETRY:          "claude-sonnet-4-6",
        TIER_ESCALATE:       "claude-opus-4-7",
        TIER_CROSS_PROVIDER: "gemini-2.5-pro",
    },
    "EMOTIONAL": {
        TIER_PRIMARY:        "claude-haiku-4-5",
        TIER_RETRY:          None,
        TIER_ESCALATE:       None,
        TIER_CROSS_PROVIDER: None,
    },
}

# Env var name for each route's primary override.
_PRIMARY_ENV = {
    "TASK_DESKTOP": "JARVIS_TASK_DESKTOP_MODEL",
    "TASK_BROWSER": "JARVIS_TASK_BROWSER_MODEL",
    "TASK_CODE":    "JARVIS_TASK_CODE_MODEL",
    "TASK_FILES":   "JARVIS_TASK_FILES_MODEL",
    "TASK_OTHER":   "JARVIS_TASK_OTHER_MODEL",
    "BANTER":       "JARVIS_BANTER_MODEL",
    "REASONING":    "JARVIS_REASONING_MODEL",
    "EMOTIONAL":    "JARVIS_EMOTIONAL_MODEL",
}


def get_primary_model(route: str) -> Optional[str]:
    """Return the route's primary model id, honoring env override.
    Returns None for unknown routes."""
    env = _PRIMARY_ENV.get(route)
    if env:
        override = os.environ.get(env, "").strip()
        if override:
            return override
    return _DEFAULTS.get(route, {}).get(TIER_PRIMARY)


def get_route_ladder(route: str) -> list[Optional[str]]:
    """Return the 4-tier ladder for a route, in order:
    [primary, retry, escalate, cross_provider].

    Env override applies to the primary slot AND propagates to the retry
    slot (since retry is conceptually the same model + force prompt).
    Kimi K2.6 substitution for TASK_BROWSER tier-2 (escalate) is honored
    when JARVIS_KIMI_VOICE_EXPERIMENTAL=1.

    Returns [None, None, None, None] for unknown routes."""
    if route not in _DEFAULTS:
        return [None, None, None, None]

    primary = get_primary_model(route)
    defaults = _DEFAULTS[route]

    # Retry slot tracks the primary (env override flows through).
    retry = primary if defaults[TIER_RETRY] is not None else None

    escalate = defaults[TIER_ESCALATE]
    # Kimi substitution: only TASK_BROWSER, only when experimental flag set.
    if route == "TASK_BROWSER" and os.environ.get(
        "JARVIS_KIMI_VOICE_EXPERIMENTAL", "0"
    ) == "1":
        escalate = "kimi-k2.6-agent"  # tool-using K2.6 variant; matches SPEECH_MODELS key

    cross = defaults[TIER_CROSS_PROVIDER]

    return [primary, retry, escalate, cross]


def routes_with_retry_chain() -> set[str]:
    """Routes whose ladder has at least one non-None retry tier
    (i.e. routes that participate in the pre-TTS confab gate's retry
    chain). BANTER + EMOTIONAL are excluded — gate bypasses them."""
    out = set()
    for route, table in _DEFAULTS.items():
        for tier in (TIER_RETRY, TIER_ESCALATE, TIER_CROSS_PROVIDER):
            if table.get(tier) is not None:
                out.add(route)
                break
    return out
