"""Log messages to the session transcript."""

from __future__ import annotations

from typing import Any, Callable, List, Optional


class MessageLogger:
    """Logs messages to the transcript incrementally.

    Tracks the last recorded position to avoid re-scanning the full list.

    Equivalent to useLogMessages React hook.
    """

    def __init__(
        self,
        record_transcript: Optional[Callable] = None,
        team_name: Optional[str] = None,
        agent_name: Optional[str] = None,
    ):
        self._record_transcript = record_transcript
        self._team_name = team_name
        self._agent_name = agent_name
        self._last_recorded_length = 0
        self._first_message_uuid: Optional[str] = None

    def log(self, messages: List[Any], ignore: bool = False) -> None:
        if ignore or not self._record_transcript:
            return

        current_first = messages[0].get("uuid") if messages else None
        prev_length = self._last_recorded_length

        is_incremental = (
            current_first is not None
            and self._first_message_uuid is not None
            and current_first == self._first_message_uuid
            and prev_length <= len(messages)
        )

        start = prev_length if is_incremental else 0
        if start == len(messages):
            return

        msg_slice = messages if start == 0 else messages[start:]
        self._record_transcript(
            msg_slice,
            team_name=self._team_name,
            agent_name=self._agent_name,
        )

        self._last_recorded_length = len(messages)
        self._first_message_uuid = current_first
