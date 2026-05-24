"""Stable/volatile system-prompt split for provider-side prefix caching.

Background
----------
JARVIS's supervisor system prompt was historically one monolithic string
assembled per session as::

    instructions_prefix + memory_block + breaker_block + skill_catalog_block

The LiveKit Anthropic plugin's auto-cache (``caching="ephemeral"``) sets
``cache_control`` on the LAST system block — so when the prompt is one
string, the cache breakpoint lands at the very end. Any change to the
volatile tail (memory write, breaker flip, runtime-id update) then
invalidates the cache for the rest of the session. Real-world measured
hit rate on ``claude-haiku-4-5``: 81% (172 turns, 140 cached) — the
remaining 19% being memory writes + breaker trips + session restarts.

This module owns the *contract* both ends share so the wrappers can
slice the prompt at the right boundary:

  STABLE PREFIX   — SOUL + JARVIS_INSTRUCTIONS + skill_catalog_block
                    (only changes when supervisor.md, soul.md, or the
                    skills inventory changes; in a normal session: stable
                    for the whole session).
  VOLATILE SUFFIX — runtime_id_block + memory_block + breaker_block
                    (changes per session, per memory write, per breaker
                    transition).

The two are joined inside the per-session ``initial_instructions`` string
that the framework hands off as the supervisor's single system message.
The wrappers that care (Anthropic, Gemini) recover the split either by
exact-prefix match against the expected stable prefix or by detecting an
embedded marker — and place their provider-specific cache breakpoint at
the boundary.

OpenAI + DeepSeek (and Groq for the legacy fallback rungs) auto-cache on
prefix-match — they don't need a wrapper to split, only that the
stable bytes come FIRST. The new key order in
``_build_initial_prompt_state`` (stable_prefix → volatile_suffix) is
what activates auto-caching for them; no per-provider wrapper required.

Why both prefix-match and marker?
---------------------------------
- Exact-prefix match (the wrapper holds the expected stable string and
  checks ``system_text.startswith(stable_prefix)``) is the fast, robust
  default — works without any sentinel in the prompt and handles
  arbitrary stable content.
- The marker ``CACHE_BREAK_MARKER`` is a belt-and-suspenders fallback
  for the case where the wrapper was constructed before the prompt
  state was assembled (e.g. ``make_speech_llm()`` runs early, before
  ``_build_initial_prompt_state``). In that path,
  ``apply_stable_prefix_recursively`` walks the constructed LLMs and
  hands them the stable prefix as soon as the prompt state exists; the
  marker is the safety net if that wiring is ever missed.

The marker text MUST never appear in any handwritten prompt content
(soul.md, supervisor.md, skill metadata, memory). It's deliberately
spelled in a way no LLM-friendly prose ever produces.
"""
from __future__ import annotations

import logging
from typing import Iterable, Optional

logger = logging.getLogger("jarvis.prompt_cache")


__all__ = [
    "CACHE_BREAK_MARKER",
    "assemble_with_marker",
    "split_system_text",
    "apply_stable_prefix_recursively",
]


# Sentinel embedded between the stable prefix and the volatile suffix in
# `initial_instructions`. Wrappers that don't know the stable prefix at
# construction time fall back to splitting on this marker. Surrounded by
# newlines in `assemble_with_marker` so the split leaves clean text on
# both sides (the wrapper trims the trailing/leading whitespace itself).
CACHE_BREAK_MARKER: str = "<<<JARVIS_CACHE_BREAK>>>"


def assemble_with_marker(stable_prefix: str, volatile_suffix: str) -> str:
    """Join the two halves with the marker between them.

    Used by `_build_initial_prompt_state` to produce the single
    `initial_instructions` string the framework stores as the supervisor's
    one-and-only system message. The marker (with newlines on both
    sides) is the deterministic split point for wrappers that don't
    know the stable prefix at construction time.

    The marker line is invisible to the user; the LLM sees it as plain
    text. For the Anthropic + Gemini paths the wrappers strip it before
    it ever reaches the model. For the OpenAI / DeepSeek / Groq paths
    no wrapper exists — the LLM sees the marker as part of its system
    prompt. That's the documented trade-off; the marker is short and
    LLMs are extremely unlikely to mention or reproduce it. If this
    ever becomes a UX issue we can add per-provider stripping shims.
    """
    if not stable_prefix and not volatile_suffix:
        return ""
    if not volatile_suffix:
        return stable_prefix
    if not stable_prefix:
        return volatile_suffix
    return f"{stable_prefix}\n{CACHE_BREAK_MARKER}\n{volatile_suffix}"


def split_system_text(
    system_text: str,
    stable_prefix: Optional[str] = None,
) -> tuple[str, str]:
    """Recover ``(stable, volatile)`` from a joined system-prompt string.

    Resolution order:
      1. **Exact-prefix match** — when ``stable_prefix`` is provided AND
         ``system_text`` starts with it, return that exact prefix as
         stable and the remainder (with any leading marker+whitespace
         stripped) as volatile. This is the cheapest, most reliable
         path; it's the default when the wrapper was given the stable
         prefix at construction time via the prompt-state wiring.
      2. **Marker split** — fall back to splitting on
         ``CACHE_BREAK_MARKER``. Used when no stable_prefix is known
         (e.g. early-constructed LLMs that never received the prompt
         state) but the assembler did embed the marker.
      3. **No split possible** — return ``(system_text, "")`` so the
         caller falls through to no-cache behaviour. Better to ship a
         non-cached request than a malformed one.

    Returns ``(stable, volatile)``. Both values are stripped of leading
    /trailing whitespace introduced by the join.
    """
    if not system_text:
        return "", ""

    if stable_prefix and system_text.startswith(stable_prefix):
        tail = system_text[len(stable_prefix):]
        # Strip the marker (if present) + any surrounding whitespace so
        # the recovered volatile is clean.
        if CACHE_BREAK_MARKER in tail:
            tail = tail.split(CACHE_BREAK_MARKER, 1)[1]
        return stable_prefix, tail.lstrip("\n").rstrip()

    if CACHE_BREAK_MARKER in system_text:
        stable, volatile = system_text.split(CACHE_BREAK_MARKER, 1)
        return stable.rstrip(), volatile.lstrip("\n").rstrip()

    # No split point recoverable — caller falls back to no-cache.
    return system_text, ""


def _walk_inner_llms(root) -> Iterable:
    """Yield each LLM instance hidden inside a DispatchingLLM /
    FallbackAdapter tree. Single LLMs yield themselves.

    Tolerates both the LiveKit FallbackAdapter shape
    (``_llm_instances``) and the older ``_llms`` shape some forks use.
    Unknown shapes yield the node itself so the caller's setter walk
    still has a chance to hit it.
    """
    if root is None:
        return
    seen: set[int] = set()
    stack = [root]
    while stack:
        node = stack.pop()
        if id(node) in seen:
            continue
        seen.add(id(node))
        # DispatchingLLM exposes its per-route inners + the fallback
        # via attributes. Recurse into both.
        inners = getattr(node, "inners", None)
        if isinstance(inners, dict):
            stack.extend(inners.values())
            fb = getattr(node, "fallback", None)
            if fb is not None:
                stack.append(fb)
            continue
        # FallbackAdapter (and forks) hold their rungs under one of two
        # private names depending on plugin version.
        rungs = (
            getattr(node, "_llm_instances", None)
            or getattr(node, "_llms", None)
        )
        if rungs:
            stack.extend(rungs)
            continue
        yield node


def apply_stable_prefix_recursively(root, stable_prefix: str) -> int:
    """Walk a DispatchingLLM / FallbackAdapter tree and hand the stable
    prefix to every wrapper that knows what to do with it.

    The contract for wrappers: they expose a ``set_stable_prefix(str)``
    method. Today that's `AnthropicCachedLLM` and `GeminiCachedLLM`;
    other LLMs (Groq, OpenAI plain, DeepSeek) silently skip — they
    either auto-cache on prefix match (no wrapper needed) or aren't
    cache-capable at all.

    Returns the number of wrappers updated. Logged at debug level so
    operators can confirm the wiring landed without grepping behaviour.
    """
    if not stable_prefix:
        return 0
    n = 0
    for inst in _walk_inner_llms(root):
        setter = getattr(inst, "set_stable_prefix", None)
        if callable(setter):
            try:
                setter(stable_prefix)
                n += 1
            except Exception as e:  # noqa: BLE001 — wrapper bugs must not break boot
                label = getattr(inst, "_jarvis_label", type(inst).__name__)
                logger.warning(
                    f"[prompt-cache] set_stable_prefix failed on {label}: "
                    f"{type(e).__name__}: {e}"
                )
    if n:
        logger.info(
            f"[prompt-cache] applied stable prefix "
            f"({len(stable_prefix)} chars) to {n} wrapper(s)"
        )
    return n
