"""Context window management for JARVIS conversations."""

from src.config import MAX_HISTORY


def build_context(history: list[dict], max_turns: int = MAX_HISTORY) -> list[dict]:
    """Build a context window from conversation history.

    Keeps the most recent turns within the limit, always preserving
    the first turn if it exists (for initial context).
    """
    if not history:
        return []

    if len(history) <= max_turns:
        return history

    # Keep first turn + most recent turns
    return [history[0]] + history[-(max_turns - 1):]
