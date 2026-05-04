"""Speculative tool prefetch — anticipate the user's tool need and
dispatch in parallel with the filler synthesis. The "uncannily fast"
property the user feels.

ONLY tools on the safe whitelist may run speculatively. Destructive
operations (clicks, sends, posts, deletes) require the user's actual
intent confirmation, so even if confidence on routing is 90%+ we
never speculatively click the "Delete account" button.

Exposes:
  - is_speculative_safe(tool_name) — guard
  - speculative_dispatch_node      — runs the dispatch in parallel
                                     (added in Task 13)
  - reconcile_speculative_result   — uses-or-discards the prefetched
                                     result based on what task_dispatch
                                     actually emits (Task 13)
"""
from __future__ import annotations

import logging
import os
import re
import uuid
from typing import Any, Optional

logger = logging.getLogger("supervisor_graph.speculative")


# Tools where the action is idempotent / view-only / non-destructive.
# Speculative dispatch of these is harmless if the prediction was wrong
# (browser opens an extra tab; result simply gets discarded).
#
# Anything not on this list is NEVER dispatched speculatively. New
# specialists default to NOT-safe — explicit opt-in only.
_SAFE_TOOLS: frozenset[str] = frozenset({
    "transfer_to_browser",   # specialist itself is safe to start;
                             # the specialist's own task_done gate
                             # ensures it only does work when needed
    "ext_navigate",
    "ext_new_tab",
    "ext_screenshot",
    "ext_observe",
    "ext_dom_summary",
    "ext_get_url",
    "ext_list_tabs",
    "ext_extract_text",
    "ext_get_console",
    "web_search",
})


def is_speculative_safe(tool_name: str) -> bool:
    """True iff `tool_name` may run speculatively before the user has
    fully expressed intent. Any non-listed tool returns False
    (default-deny)."""
    return tool_name in _SAFE_TOOLS


SPEC_PREFETCH_THRESHOLD = float(
    os.environ.get("JARVIS_SPEC_PREFETCH_THRESHOLD", "0.7")
)


# Lightweight verb→tool prediction. For the soak window this is
# regex-based: when the user says "open <X>", predict
# transfer_to_browser. Replaceable later with a small LLM call if
# accuracy plateaus.
_VERB_TO_TOOL = (
    (re.compile(r"\b(?:open|launch|go\s+to|navigate|visit|browse)\b", re.I),
     "transfer_to_browser"),
    (re.compile(r"\bsearch\b", re.I), "transfer_to_browser"),
    (re.compile(r"\bscreenshot\b", re.I), "ext_screenshot"),
    (re.compile(r"\bwhat'?s?\s+on\s+(?:my\s+)?screen\b", re.I), "ext_dom_summary"),
)


def _predict_tool(user_query: str) -> Optional[str]:
    """Predict the most likely tool the user wants. Returns None when
    no pattern matches (in which case speculative_dispatch_node will
    skip the prefetch). Replaceable with a learned predictor."""
    if not user_query:
        return None
    for pattern, tool in _VERB_TO_TOOL:
        if pattern.search(user_query):
            return tool
    return None


def speculative_dispatch_node(state: dict) -> dict:
    """Decide whether to fire a speculative dispatch. Sets
    `speculative_dispatch_id` on the state if it does.

    The actual dispatch is initiated here but its result lands later
    via `speculative_result`. The reconcile step (after task_dispatch)
    decides whether to use the cached result.
    """
    if state.get("route") != "TASK":
        return {}
    if state.get("route_confidence", 0.0) < SPEC_PREFETCH_THRESHOLD:
        return {}
    if state.get("failed_providers"):
        # Don't speculate during recovery — could amplify failures.
        return {}

    predicted = _predict_tool(state.get("user_query", ""))
    if predicted is None:
        return {}
    if not is_speculative_safe(predicted):
        logger.info(
            "[speculative] predicted tool %r is not safe; skipping",
            predicted,
        )
        return {}

    dispatch_id = f"spec_{uuid.uuid4().hex[:8]}"
    logger.info(
        "[speculative] dispatching %r (id=%s) for query=%r",
        predicted, dispatch_id, state.get("user_query", "")[:80],
    )
    # NOTE: actual asynchronous dispatch is wired in Task 15 when this
    # node is integrated into the graph. For now we just record the
    # intent so reconcile_speculative_result has something to compare
    # against. The real dispatch will be done by the LLM adapter,
    # which can fire-and-forget while task_dispatch_node runs.
    return {
        "speculative_dispatch_id": dispatch_id,
        "speculative_result": {
            "tool": predicted,
            "args": {"request": state.get("user_query", "")},
            "result": None,  # populated by the adapter when it returns
            "ok": None,
        },
    }


def reconcile_speculative_result(
    state: dict, real_call: dict[str, Any],
) -> dict[str, bool]:
    """After task_dispatch_node emits the real tool_call, decide whether
    to use the speculative result or discard it.

    Returns {"use_cached": bool}.
    """
    spec_id = state.get("speculative_dispatch_id")
    spec_result = state.get("speculative_result")
    if spec_id is None or spec_result is None:
        return {"use_cached": False}
    spec_tool = spec_result.get("tool")
    real_tool = real_call.get("name")
    use_cached = spec_tool == real_tool
    logger.info(
        "[speculative] reconcile: spec=%r real=%r → use_cached=%s",
        spec_tool, real_tool, use_cached,
    )
    return {"use_cached": use_cached}
