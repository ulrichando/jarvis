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
