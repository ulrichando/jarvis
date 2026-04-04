"""Auto-evolved shortcuts — this file is rewritten by the evolution engine.

DO NOT EDIT MANUALLY — changes will be overwritten on next evolution cycle.
"""

import datetime


def check_shortcut(query: str) -> str | None:
    """Check if a query matches a known shortcut. Returns response or None."""
    q = query.lower().strip()

    # Time — only match actual time requests, not math with "times"
    if ("time" in q and "times" not in q) and any(w in q for w in ["what", "current", "show", "tell"]):
        return "[show:time]"
    if "clock" in q:
        return "[show:time]"

    # Date
    if "date" in q and any(w in q for w in ["what", "today", "current"]):
        return datetime.date.today().strftime("%A, %B %d, %Y")

    return None
