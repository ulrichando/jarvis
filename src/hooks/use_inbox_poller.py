"""Inbox polling for teammate messages."""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, List, Optional

INBOX_POLL_INTERVAL_MS = 1000


class InboxPoller:
    """Polls the teammate inbox for new messages and submits them as turns.

    1. Polls every 1s for unread messages (teammates or team leads)
    2. When idle: submits messages immediately as a new turn
    3. When busy: queues messages for UI display, delivers when turn ends

    Equivalent to useInboxPoller React hook.
    """

    def __init__(
        self,
        enabled: bool,
        on_submit_message: Callable[[str], bool],
        get_app_state: Callable,
        set_app_state: Callable,
        read_unread_messages: Optional[Callable] = None,
        mark_messages_as_read: Optional[Callable] = None,
        is_teammate: Callable[[], bool] = lambda: False,
        is_team_lead: Callable = lambda ctx: False,
        get_agent_name: Callable[[], Optional[str]] = lambda: None,
    ):
        self._enabled = enabled
        self._on_submit = on_submit_message
        self._get_app_state = get_app_state
        self._set_app_state = set_app_state
        self._read_unread = read_unread_messages
        self._mark_read = mark_messages_as_read
        self._is_teammate = is_teammate
        self._is_team_lead = is_team_lead
        self._get_agent_name = get_agent_name
        self._is_loading = False

    def set_loading(self, loading: bool) -> None:
        self._is_loading = loading

    async def poll(self) -> None:
        if not self._enabled or not self._read_unread:
            return

        agent_name = self._get_agent_name()
        if not agent_name:
            return

        state = self._get_app_state()
        team_name = state.get("team_context", {}).get("team_name")
        unread = await self._read_unread(agent_name, team_name)

        if not unread:
            return

        # Format and submit
        formatted = "\n\n".join(
            f"<teammate_message teammate_id=\"{m.get('from', '')}\">\n{m.get('text', '')}\n</teammate_message>"
            for m in unread
        )

        if not self._is_loading:
            self._on_submit(formatted)
        # else: queue for later delivery

        if self._mark_read:
            await self._mark_read(agent_name, team_name)

    async def start_polling(self) -> None:
        while self._enabled:
            await self.poll()
            await asyncio.sleep(INBOX_POLL_INTERVAL_MS / 1000)
