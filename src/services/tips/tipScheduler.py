"""Tip scheduling -- selects and shows tips during spinner."""

from __future__ import annotations

from typing import Optional

from .tipHistory import get_sessions_since_last_shown, record_tip_shown
from .tipRegistry import Tip, TipContext, get_relevant_tips


def select_tip_with_longest_time_since_shown(
    available_tips: list[Tip],
) -> Optional[Tip]:
    """Select the tip that hasn't been shown for the longest time."""
    if not available_tips:
        return None
    if len(available_tips) == 1:
        return available_tips[0]

    tips_with_sessions = [
        (tip, get_sessions_since_last_shown(tip.id))
        for tip in available_tips
    ]
    tips_with_sessions.sort(key=lambda x: x[1], reverse=True)
    return tips_with_sessions[0][0]


async def get_tip_to_show_on_spinner(
    context: Optional[TipContext] = None,
) -> Optional[Tip]:
    """Get a tip to display during the spinner."""
    tips = await get_relevant_tips(context)
    if not tips:
        return None
    return select_tip_with_longest_time_since_shown(tips)


def record_shown_tip(tip: Tip) -> None:
    """Record that a tip was shown."""
    record_tip_shown(tip.id)
