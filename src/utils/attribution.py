"""Attribution text utilities for commits and PRs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

PRODUCT_URL = "https://github.com/ulrich/jarvis"


@dataclass
class AttributionTexts:
    commit: str
    pr: str


def get_attribution_texts(model_name: str = "JARVIS") -> AttributionTexts:
    """Returns attribution text for commits and PRs."""
    default_commit = f"Co-Authored-By: {model_name} <noreply@jarvis.local>"
    default_pr = f"Generated with [JARVIS]({PRODUCT_URL})"
    return AttributionTexts(commit=default_commit, pr=default_pr)


def count_user_prompts_in_messages(
    messages: list[dict[str, Any]],
) -> int:
    """Count user messages with visible text content."""
    count = 0
    for msg in messages:
        if msg.get("type") != "user":
            continue
        content = msg.get("message", {}).get("content")
        if not content:
            continue
        if isinstance(content, str) and content.strip():
            count += 1
        elif isinstance(content, list):
            has_text = any(
                isinstance(b, dict) and b.get("type") in ("text", "image", "document")
                for b in content
            )
            if has_text:
                count += 1
    return count


async def get_enhanced_pr_attribution(model_name: str = "JARVIS") -> str:
    """Get enhanced PR attribution text with contribution stats."""
    return f"Generated with [JARVIS]({PRODUCT_URL})"
