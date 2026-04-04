"""
Format an ISO timestamp for the brief/chat message label line.

Display scales with age (like a messaging app):
  - same day:      "1:30 PM"
  - within 6 days: "Sunday, 4:15 PM"
  - older:         "Sunday, Feb 20, 4:30 PM"
"""

from __future__ import annotations

import locale
import os
from datetime import datetime, timezone
from typing import Optional


def format_brief_timestamp(
    iso_string: str, now: Optional[datetime] = None
) -> str:
    """Format a timestamp for brief display, scaling with age."""
    try:
        d = datetime.fromisoformat(iso_string.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return ""

    if now is None:
        now = datetime.now(tz=d.tzinfo or timezone.utc)

    # Make both timezone-aware or naive for comparison
    if d.tzinfo and not now.tzinfo:
        now = now.replace(tzinfo=d.tzinfo)
    elif now.tzinfo and not d.tzinfo:
        d = d.replace(tzinfo=now.tzinfo)

    d_local = d.astimezone() if d.tzinfo else d
    now_local = now.astimezone() if now.tzinfo else now

    day_diff = (now_local.date() - d_local.date()).days

    if day_diff == 0:
        return d_local.strftime("%-I:%M %p")

    if 0 < day_diff < 7:
        return d_local.strftime("%A, %-I:%M %p")

    return d_local.strftime("%A, %b %-d, %-I:%M %p")
