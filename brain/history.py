"""
Per-channel conversation history with hard turn cap and tool result summarization.
Keeps context windows lean — raw tool JSON is never stored.
"""

import logging
from collections import defaultdict
from typing import Any

logger = logging.getLogger(__name__)


class ChannelHistory:
    """
    Stores the last MAX_TURNS user+assistant pairs per channel.
    Tool results are summarized before storage to avoid context bloat.
    History is trimmed BEFORE each API call, not after.
    """

    MAX_TURNS: int = 8  # max user+assistant pairs to keep per channel

    def __init__(self) -> None:
        self._store: dict[str, list[dict[str, str]]] = defaultdict(list)

    def get(self, channel_id: str) -> list[dict[str, str]]:
        """
        Return a trimmed copy of conversation history for use in API messages param.
        Trimming happens here so the returned slice is always within budget.
        """
        history = self._store[channel_id]
        max_messages = self.MAX_TURNS * 2
        return list(history[-max_messages:])

    def add_user(self, channel_id: str, message: str) -> None:
        """Append a user turn to history."""
        self._store[channel_id].append({"role": "user", "content": message})

    def add_assistant(self, channel_id: str, response: str) -> None:
        """Append an assistant turn to history."""
        self._store[channel_id].append({"role": "assistant", "content": response})

    def add_tool_result(
        self, channel_id: str, tool_name: str, result: dict[str, Any]
    ) -> None:
        """
        Summarize a tool result before storing. Raw JSON is never kept in history
        because it re-inflates the context window on every subsequent API call.
        """
        summary = self._summarize_tool_result(tool_name, result)
        self._store[channel_id].append({
            "role":    "assistant",
            "content": f"[Tool result: {tool_name}] {summary}",
        })

    def clear(self, channel_id: str) -> None:
        """Clear all history for a channel (e.g. on a 'forget that' command)."""
        self._store[channel_id] = []
        logger.info(f"[history] cleared channel={channel_id}")

    def _summarize_tool_result(self, tool_name: str, result: dict[str, Any]) -> str:
        """
        Convert raw tool JSON into a short human-readable summary.
        Avoids storing structured dicts that waste tokens on every future call.
        """
        try:
            if tool_name == "get_weather":
                return (
                    f"{result.get('city', '?')}: "
                    f"{result.get('temp', '?')}°{result.get('units', 'C')[0].upper()}, "
                    f"{result.get('condition', '?')}"
                )
            if tool_name == "web_search":
                results = result.get("results", [])
                if not results:
                    return "No results found."
                top = results[0].get("title", "?")
                return f"Found {len(results)} results. Top: {top}"
            if tool_name == "set_reminder":
                return f"Reminder set for {result.get('time', '?')}: {result.get('task', '?')}"
            if tool_name == "play_music":
                return f"Now playing: {result.get('track', result.get('query', '?'))}"
            if tool_name == "open_app":
                return f"Launched: {result.get('app', '?')}"
            if tool_name == "system_control":
                return f"System action '{result.get('action', '?')}' completed."
            # Fallback for unknown tools — truncate to avoid bloat
            return str(result)[:200]
        except Exception:
            return "Tool completed."
