"""Common constants and utility functions."""

import os
from datetime import datetime
from functools import lru_cache


def get_local_iso_date() -> str:
    """Get the LOCAL date in ISO format."""
    override = os.environ.get("CLAUDE_CODE_OVERRIDE_DATE")
    if override:
        return override

    now = datetime.now()
    return now.strftime("%Y-%m-%d")


@lru_cache(maxsize=1)
def get_session_start_date() -> str:
    """Memoized session start date for prompt-cache stability."""
    return get_local_iso_date()


def get_local_month_year() -> str:
    """Returns 'Month YYYY' (e.g. 'February 2026') in the user's local timezone."""
    override = os.environ.get("CLAUDE_CODE_OVERRIDE_DATE")
    if override:
        date = datetime.fromisoformat(override)
    else:
        date = datetime.now()
    return date.strftime("%B %Y")
