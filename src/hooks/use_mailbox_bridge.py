"""Bridge between mailbox messages and REPL submission."""

from __future__ import annotations

from typing import Callable, Optional, Protocol


class Mailbox(Protocol):
    def subscribe(self, callback: Callable) -> Callable: ...
    def poll(self) -> Optional[dict]: ...
    @property
    def revision(self) -> int: ...


class MailboxBridge:
    """Bridges mailbox messages to REPL submission when idle.

    Equivalent to useMailboxBridge React hook.
    """

    def __init__(
        self,
        mailbox: Mailbox,
        on_submit_message: Callable[[str], bool],
    ):
        self._mailbox = mailbox
        self._on_submit_message = on_submit_message
        self._is_loading = False

    def set_loading(self, loading: bool) -> None:
        self._is_loading = loading

    def check(self) -> None:
        if self._is_loading:
            return
        msg = self._mailbox.poll()
        if msg:
            self._on_submit_message(msg.get("content", ""))
