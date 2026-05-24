"""Permission bridging between JARVIS tool calls and the IDE's approval UI.

For sensitive operations (file writes, terminal commands) the adapter
fires an ACP ``session/request_permission`` and waits for the user's
choice in their IDE. The mapping mirrors the upstream ACP spec:

  - ``allow_once`` / ``allow_session`` / ``allow_always`` → permit
  - ``deny`` / ``deny_always`` → block (the tool result becomes an error
    string the supervisor LLM sees and acknowledges).

A single ``JARVIS_ACP_PERMISSIONS=permissive`` env override skips the
round-trip entirely. Use it for headless soak tests or power-user
sessions where the back-and-forth would be friction.
"""

from __future__ import annotations

import asyncio
import logging
import os
from concurrent.futures import TimeoutError as FutureTimeout
from itertools import count
from typing import Awaitable, Callable

from acp.schema import AllowedOutcome, PermissionOption

logger = logging.getLogger(__name__)


# Outcome ids the adapter understands. Stable across both the "with
# permanent" and "without permanent" option lists below.
_OPTION_ID_TO_DECISION = {
    "allow_once": "allow",
    "allow_session": "allow",
    "allow_always": "allow",
    "deny": "deny",
    "deny_always": "deny",
}

_PERMISSION_REQUEST_IDS = count(1)


def is_permissive_mode() -> bool:
    """Return True when ``JARVIS_ACP_PERMISSIONS=permissive`` is set.

    Permissive mode short-circuits every approval to ``allow_once`` so
    headless tests / power-user sessions don't stall waiting for an IDE
    that never answers. Read at call time (not import) so flipping the
    env at runtime takes effect on the next prompt.
    """
    return os.environ.get("JARVIS_ACP_PERMISSIONS", "").strip().lower() == "permissive"


def _permission_option_supports_kind(kind: str) -> bool:
    """Return whether the installed ACP SDK accepts a permission option kind."""
    try:
        PermissionOption(option_id="__probe__", kind=kind, name="probe")
    except Exception:
        return False
    return True


def _build_permission_options(*, allow_permanent: bool) -> list[PermissionOption]:
    """Build an option list matching JARVIS's approval semantics."""
    options = [
        PermissionOption(option_id="allow_once", kind="allow_once", name="Allow once"),
        PermissionOption(
            option_id="allow_session",
            # ACP has no session-scoped kind; use the closest persistent
            # hint and keep JARVIS semantics in the option_id.
            kind="allow_always",
            name="Allow for session",
        ),
    ]
    if allow_permanent:
        options.append(
            PermissionOption(
                option_id="allow_always",
                kind="allow_always",
                name="Allow always",
            )
        )
    options.append(PermissionOption(option_id="deny", kind="reject_once", name="Deny"))
    if _permission_option_supports_kind("reject_always"):
        options.append(
            PermissionOption(
                option_id="deny_always",
                kind="reject_always",
                name="Deny always",
            )
        )
    return options


def _build_permission_tool_call(command: str, description: str):
    """Build the ``ToolCallUpdate`` payload attached to a permission request."""
    import acp as _acp

    tool_call_id = f"perm-check-{next(_PERMISSION_REQUEST_IDS)}"
    title = f"{description}: {command}" if description else command
    body = f"{description}\n$ {command}" if description else f"$ {command}"
    return _acp.update_tool_call(
        tool_call_id,
        title=title,
        kind="execute",
        status="pending",
        content=[_acp.tool_content(_acp.text_block(body))],
        raw_input={"command": command, "description": description},
    )


def _decision_for_outcome(outcome: object, *, allowed_option_ids: set[str]) -> str:
    """Translate an ACP ``RequestPermissionResponse.outcome`` to allow/deny."""
    if not isinstance(outcome, AllowedOutcome):
        return "deny"
    option_id = outcome.option_id
    if option_id not in allowed_option_ids:
        logger.warning("Permission request returned unknown option_id: %s", option_id)
        return "deny"
    return _OPTION_ID_TO_DECISION.get(option_id, "deny")


def make_approval_callback(
    request_permission_fn: Callable[..., Awaitable[object]],
    loop: asyncio.AbstractEventLoop,
    session_id: str,
    timeout: float = 60.0,
) -> Callable[..., str]:
    """Return a sync ``(command, description) -> allow|deny`` callback.

    Tools invoked from worker threads use this to ask the IDE for
    approval. The ACP connection lives on the supervisor's event loop;
    we schedule the coroutine there with ``run_coroutine_threadsafe`` and
    block the worker until either the user answers, the timeout fires,
    or the request raises (any of which deny).
    """

    def _callback(
        command: str,
        description: str = "",
        *,
        allow_permanent: bool = True,
        **_: object,
    ) -> str:
        if is_permissive_mode():
            return "allow"

        options = _build_permission_options(allow_permanent=allow_permanent)
        tool_call = _build_permission_tool_call(command, description)
        coro = request_permission_fn(
            session_id=session_id,
            tool_call=tool_call,
            options=options,
        )
        try:
            future = asyncio.run_coroutine_threadsafe(coro, loop)
        except Exception as exc:
            logger.warning("Could not schedule permission request: %s", exc)
            return "deny"

        try:
            response = future.result(timeout=timeout)
        except (FutureTimeout, Exception) as exc:
            future.cancel()
            logger.warning("Permission request timed out or failed: %s", exc)
            return "deny"

        if response is None:
            return "deny"

        allowed_ids = {opt.option_id for opt in options}
        return _decision_for_outcome(
            getattr(response, "outcome", None),
            allowed_option_ids=allowed_ids,
        )

    return _callback
