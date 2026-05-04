"""Evidence-finder helpers tuned for the grounding gate.

The grounding gate's job is to validate past-tense claims in supervisor
output. The primary question it asks the blackboard is:

  "Is there a recent ToolResult that corroborates the claim '<verb>
   <object>' the supervisor is about to speak?"

`find_tool_evidence` answers that. The match is keyword-overlap based
(claim keywords vs ToolResult.tool, args, and result string), bounded
to a recent time window so old evidence doesn't validate fresh lies.
"""
from __future__ import annotations

import time
from typing import Optional

from .client import BlackboardClient
from .schema import ToolResult


def find_tool_evidence(
    client: BlackboardClient,
    *,
    claim_keywords: list[str],
    within_seconds: int = 30,
) -> Optional[ToolResult]:
    """Find the most recent successful ToolResult whose tool name,
    args, or result string matches ANY of `claim_keywords`. Returns
    None if no match is recent enough (within `within_seconds`).

    Match is case-insensitive substring containment. A claim
    "opened" matches a tool named "ext_new_tab" via the result string
    "ok: tab opened" or via tool="ext_new_tab" containing "open".
    """
    if not claim_keywords:
        return None
    cutoff = time.time() - within_seconds
    keywords_lower = [k.lower() for k in claim_keywords]
    for r in client.recent_tools(limit=10):
        if r.ts < cutoff:
            continue
        if not r.ok:
            continue  # failures don't validate past-tense claims
        haystack = " ".join([
            r.tool.lower(),
            " ".join(str(v).lower() for v in r.args.values()),
            r.result.lower(),
        ])
        if any(kw in haystack for kw in keywords_lower):
            return r
    return None


def has_recent_tool(
    client: BlackboardClient,
    *,
    tool_name: str,
    within_seconds: int = 30,
) -> bool:
    """True if any successful ToolResult with tool=tool_name occurred
    within the time window."""
    cutoff = time.time() - within_seconds
    for r in client.recent_tools(limit=10):
        if r.ts < cutoff:
            continue
        if r.ok and r.tool == tool_name:
            return True
    return False
