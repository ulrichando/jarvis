"""Edit-approval bridge so file mutations round-trip through the IDE UI.

JARVIS's file-write tools (``write_file``, ``patch``) execute the change
synchronously in their handlers. For ACP we want the user to see the
proposed diff in their IDE and click approve/deny before the write
lands. This module builds the ACP diff payloads and asks the connected
client via ``session/request_permission``.

The adapter applies edit approval at the ACP layer (around the tool
handler) rather than patching the handlers themselves — JARVIS's voice
and CLI surfaces never want IDE-style approval prompts, so this stays
strictly opt-in via the ACP loop.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from concurrent.futures import TimeoutError as FutureTimeout
from contextvars import ContextVar, Token
from dataclasses import dataclass
from itertools import count
from pathlib import Path
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


# Edits to files matching these names always prompt even under
# auto-approve policies. Belt and suspenders against an LLM that wants
# to overwrite a secret file because the user's session policy says
# "approve everything in workspace".
SENSITIVE_AUTO_APPROVE_NAMES = {".env", ".env.local", ".env.production", "id_rsa", "id_ed25519"}

AUTO_APPROVE_ASK = "ask"
AUTO_APPROVE_WORKSPACE = "workspace_session"
AUTO_APPROVE_SESSION = "session"


@dataclass(frozen=True)
class EditProposal:
    """A single proposed file edit ready to render in the IDE."""

    tool_name: str
    path: str
    old_text: str | None
    new_text: str
    arguments: dict[str, Any]


EditApprovalRequester = Callable[[EditProposal], bool]

_EDIT_APPROVAL_REQUESTER: ContextVar[EditApprovalRequester | None] = ContextVar(
    "ACP_EDIT_APPROVAL_REQUESTER",
    default=None,
)
_PERMISSION_REQUEST_IDS = count(1)


def set_edit_approval_requester(requester: EditApprovalRequester | None) -> Token:
    """Bind a requester for the duration of one ACP prompt turn."""
    return _EDIT_APPROVAL_REQUESTER.set(requester)


def reset_edit_approval_requester(token: Token) -> None:
    """Restore the previous requester binding (paired with ``set_``)."""
    _EDIT_APPROVAL_REQUESTER.reset(token)


def get_edit_approval_requester() -> EditApprovalRequester | None:
    """Return the currently-bound requester, or None when unset."""
    return _EDIT_APPROVAL_REQUESTER.get()


def _read_text_if_exists(path: str) -> str | None:
    """Read a file as UTF-8 text, returning None when absent."""
    p = Path(path).expanduser()
    if not p.exists():
        return None
    if not p.is_file():
        raise OSError(f"Cannot edit non-file path: {path}")
    return p.read_text(encoding="utf-8", errors="replace")


def _proposal_for_write_file(arguments: dict[str, Any]) -> EditProposal:
    path = str(arguments.get("path") or "")
    if not path:
        raise ValueError("path required")
    content = arguments.get("content")
    if content is None:
        raise ValueError("content required")
    return EditProposal(
        tool_name="write_file",
        path=path,
        old_text=_read_text_if_exists(path),
        new_text=str(content),
        arguments=dict(arguments),
    )


def _proposal_for_patch_replace(arguments: dict[str, Any]) -> EditProposal | None:
    """Build a diff preview for a ``patch`` call (replace mode only).

    JARVIS's patch tool supports several modes; we only round-trip the
    common ``replace`` shape here. Other modes (``append`` /
    ``insert_after``) fall through to plain text approval at the tool
    level — fixing that is a follow-up once those shapes land in the
    voice flow.
    """
    path = str(arguments.get("path") or "")
    if not path:
        raise ValueError("path required")
    old_string = arguments.get("old_string")
    new_string = arguments.get("new_string")
    if old_string is None or new_string is None:
        # Not enough info to render a diff; let the generic permission
        # path handle the call instead.
        return None

    old_text = _read_text_if_exists(path)
    if old_text is None:
        raise ValueError(f"Cannot read file for patch preview: {path}")
    # Best-effort: replace the literal old text. JARVIS's patch tool
    # itself does fuzzy matching (whitespace tolerance, etc.); we render
    # the user-facing diff against the literal request so the IDE shows
    # exactly what the LLM asked for.
    if str(old_string) in old_text:
        if arguments.get("replace_all"):
            new_text = old_text.replace(str(old_string), str(new_string))
        else:
            new_text = old_text.replace(str(old_string), str(new_string), 1)
    else:
        # No literal match; surface a hint in the diff so the reviewer
        # can decide whether to approve a fuzzy-match write.
        new_text = old_text + (
            f"\n\n# [JARVIS ACP] Note: literal old_string not found; "
            f"the tool will fuzzy-match.\n# Requested replacement:\n"
            f"# - {old_string!r}\n# + {new_string!r}\n"
        )

    return EditProposal(
        tool_name="patch",
        path=path,
        old_text=old_text,
        new_text=new_text,
        arguments=dict(arguments),
    )


def build_edit_proposal(tool_name: str, arguments: dict[str, Any]) -> EditProposal | None:
    """Return a diff proposal for supported mutate-the-file calls."""
    if tool_name == "write_file":
        return _proposal_for_write_file(arguments)
    if tool_name == "patch" and arguments.get("mode", "replace") == "replace":
        return _proposal_for_patch_replace(arguments)
    return None


def _is_sensitive_auto_approve_path(path: str) -> bool:
    """Sensitive paths always prompt regardless of policy."""
    parts = Path(path).expanduser().parts
    lowered = {part.lower() for part in parts}
    if ".git" in lowered or ".ssh" in lowered:
        return True
    return Path(path).name.lower() in SENSITIVE_AUTO_APPROVE_NAMES


def should_auto_approve_edit(proposal: EditProposal, policy: str, cwd: str | None = None) -> bool:
    """Return whether this edit may skip the prompt under the given policy."""
    policy = str(policy or AUTO_APPROVE_ASK).strip()
    if policy == AUTO_APPROVE_ASK or _is_sensitive_auto_approve_path(proposal.path):
        return False
    path = Path(proposal.path).expanduser().resolve(strict=False)
    if policy == AUTO_APPROVE_SESSION:
        return True
    if policy == AUTO_APPROVE_WORKSPACE:
        # tempfile.gettempdir() is the cross-platform per-user temp dir;
        # using a literal "/tmp" misses macOS (/private/tmp) and Windows.
        tmp_root = Path(tempfile.gettempdir()).resolve(strict=False)
        try:
            path.relative_to(tmp_root)
            return True
        except ValueError:
            pass
        if cwd:
            root = Path(cwd).expanduser().resolve(strict=False)
            try:
                path.relative_to(root)
                return True
            except ValueError:
                return False
    return False


def maybe_require_edit_approval(tool_name: str, arguments: dict[str, Any]) -> str | None:
    """Run an ACP edit-approval round-trip when one is bound.

    Returns a JSON error string when the edit must be blocked; ``None``
    otherwise so the tool handler runs normally. Exceptions during
    requester invocation deny by default (fail-safe).
    """
    requester = get_edit_approval_requester()
    if requester is None:
        return None
    if os.environ.get("JARVIS_ACP_PERMISSIONS", "").strip().lower() == "permissive":
        return None

    try:
        proposal = build_edit_proposal(tool_name, arguments)
    except Exception as exc:
        logger.warning("Could not build ACP edit proposal for %s: %s", tool_name, exc)
        import json

        return json.dumps({"error": f"Edit approval denied: could not prepare diff ({exc})"}, ensure_ascii=False)

    if proposal is None:
        return None

    try:
        approved = bool(requester(proposal))
    except Exception as exc:
        logger.warning("ACP edit approval requester failed: %s", exc)
        approved = False

    if approved:
        return None
    import json

    return json.dumps(
        {"error": "Edit approval denied by ACP client; file was not modified."},
        ensure_ascii=False,
    )


def build_acp_edit_tool_call(proposal: EditProposal):
    """Build the ``ToolCallUpdate`` payload for ``request_permission``."""
    import acp

    tool_call_id = f"edit-approval-{next(_PERMISSION_REQUEST_IDS)}"
    return acp.update_tool_call(
        tool_call_id,
        title=f"Approve edit: {proposal.path}",
        kind="edit",
        status="pending",
        content=[
            acp.tool_diff_content(
                path=proposal.path,
                old_text=proposal.old_text,
                new_text=proposal.new_text,
            )
        ],
        raw_input={"tool": proposal.tool_name, "arguments": proposal.arguments},
    )


def make_acp_edit_approval_requester(
    request_permission_fn: Callable[..., Awaitable[object]],
    loop: asyncio.AbstractEventLoop,
    session_id: str,
    timeout: float = 60.0,
    auto_approve_getter: Callable[[], tuple[str, str | None]] | None = None,
) -> EditApprovalRequester:
    """Return a sync requester that bridges edit proposals to ACP permissions."""

    def _requester(proposal: EditProposal) -> bool:
        from acp.schema import PermissionOption

        if auto_approve_getter is not None:
            try:
                policy, cwd = auto_approve_getter()
                if should_auto_approve_edit(proposal, policy, cwd):
                    logger.info(
                        "Auto-approved ACP edit under policy %s: %s", policy, proposal.path
                    )
                    return True
            except Exception:
                logger.debug("ACP edit auto-approval check failed", exc_info=True)

        options = [
            PermissionOption(option_id="allow_once", kind="allow_once", name="Allow edit"),
            PermissionOption(option_id="deny", kind="reject_once", name="Deny"),
        ]
        tool_call = build_acp_edit_tool_call(proposal)
        coro = request_permission_fn(
            session_id=session_id,
            tool_call=tool_call,
            options=options,
        )
        try:
            future = asyncio.run_coroutine_threadsafe(coro, loop)
        except Exception as exc:
            logger.warning("Could not schedule edit approval request: %s", exc)
            return False
        try:
            response = future.result(timeout=timeout)
        except (FutureTimeout, Exception) as exc:
            future.cancel()
            logger.warning("Edit approval request timed out or failed: %s", exc)
            return False
        outcome = getattr(response, "outcome", None)
        return (
            getattr(outcome, "outcome", None) == "selected"
            and getattr(outcome, "option_id", None) == "allow_once"
        )

    return _requester
