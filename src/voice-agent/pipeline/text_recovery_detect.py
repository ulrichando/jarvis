"""Pure content-block inspector for the post-tool reply-required gate.

Lives outside jarvis_agent.py so it can be unit-tested without
instantiating an AgentSession. Returns one of four classifications:

  - "final_reply": item has text, no tool_use → cancel heartbeat,
    no recovery needed
  - "interstitial": item has tool_use (with or without text) → more
    LLM iterations coming, keep heartbeat running
  - "silent_failure": item has no text AND no tool_use AND prior tool
    calls fired this turn → the LLM gave up; trigger text recovery
  - "benign_empty": item has no text AND no tool_use AND no tool calls
    fired this turn → degenerate but not the failure mode we care about
"""
from __future__ import annotations

from typing import Any


def _block_has_text(b: Any) -> bool:
    """True iff `b` represents a text block with non-whitespace content."""
    if b is None:
        return False
    if isinstance(b, str):
        return bool(b.strip())
    if isinstance(b, dict):
        if b.get("type") == "text":
            return bool((b.get("text") or "").strip())
        return False
    # Typed-object shape (livekit-agents): .type == "text", .text = "..."
    btype = getattr(b, "type", None)
    if btype == "text":
        return bool((getattr(b, "text", None) or "").strip())
    return False


def _block_is_tool_use(b: Any) -> bool:
    """True iff `b` represents a tool_use block."""
    if b is None or isinstance(b, str):
        return False
    if isinstance(b, dict):
        return b.get("type") == "tool_use"
    return getattr(b, "type", None) == "tool_use"


def classify_assistant_item(
    *,
    content: Any,
    had_prior_tool_calls: bool,
) -> str:
    """Classify an assistant conversation item. See module docstring
    for the four return values.

    `content` is item.content (livekit-agents) — may be None, a list of
    typed blocks, a list of strings, a list of dicts, or a mixed list.
    `had_prior_tool_calls` reflects session._jarvis_tool_calls_this_turn
    being non-empty at the moment this item lands.
    """
    blocks = content or []
    if not isinstance(blocks, list):
        # Defensive — content is meant to be a list. Treat singletons
        # the same as a one-element list.
        blocks = [blocks]

    has_text = any(_block_has_text(b) for b in blocks)
    has_tool_use = any(_block_is_tool_use(b) for b in blocks)

    if has_tool_use:
        # tool_use present → interstitial, regardless of text presence.
        return "interstitial"
    if has_text:
        return "final_reply"
    # No text, no tool_use.
    if had_prior_tool_calls:
        return "silent_failure"
    return "benign_empty"
