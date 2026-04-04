"""
Python equivalent of hooks.ts

Hook lifecycle types, schemas, and result structures.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Literal,
    Optional,
    Protocol,
    Union,
)

# Forward-reference aliases for external types
Message = Any
AppState = Any
AttributionState = Any
HookEvent = str
HookInput = Any
PermissionUpdate = Any
HookJSONOutput = Any
AsyncHookJSONOutput = Any
SyncHookJSONOutput = Any
PermissionResultBehavior = str  # 'ask' | 'deny' | 'allow' | 'passthrough'


# Hook event constants
HOOK_EVENTS: List[str] = [
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "UserPromptSubmit",
    "SessionStart",
    "Setup",
    "SubagentStart",
    "PermissionDenied",
    "Notification",
    "PermissionRequest",
    "Elicitation",
    "ElicitationResult",
    "CwdChanged",
    "FileChanged",
    "WorktreeCreate",
]


def is_hook_event(value: str) -> bool:
    """Check if a string is a valid hook event name."""
    return value in HOOK_EVENTS


# --------------------------------------------------------------------------
# Prompt elicitation protocol
# --------------------------------------------------------------------------

@dataclass
class PromptOption:
    key: str = ""
    label: str = ""
    description: Optional[str] = None


@dataclass
class PromptRequest:
    prompt: str = ""  # request id
    message: str = ""
    options: List[PromptOption] = field(default_factory=list)


@dataclass
class PromptResponse:
    prompt_response: str = ""  # request id
    selected: str = ""


# --------------------------------------------------------------------------
# Sync hook response (mirrors the Zod schema)
# --------------------------------------------------------------------------

@dataclass
class PreToolUseHookOutput:
    hook_event_name: Literal["PreToolUse"] = "PreToolUse"
    permission_decision: Optional[str] = None  # PermissionBehavior
    permission_decision_reason: Optional[str] = None
    updated_input: Optional[Dict[str, Any]] = None
    additional_context: Optional[str] = None


@dataclass
class UserPromptSubmitHookOutput:
    hook_event_name: Literal["UserPromptSubmit"] = "UserPromptSubmit"
    additional_context: Optional[str] = None


@dataclass
class SessionStartHookOutput:
    hook_event_name: Literal["SessionStart"] = "SessionStart"
    additional_context: Optional[str] = None
    initial_user_message: Optional[str] = None
    watch_paths: Optional[List[str]] = None


@dataclass
class SetupHookOutput:
    hook_event_name: Literal["Setup"] = "Setup"
    additional_context: Optional[str] = None


@dataclass
class SubagentStartHookOutput:
    hook_event_name: Literal["SubagentStart"] = "SubagentStart"
    additional_context: Optional[str] = None


@dataclass
class PostToolUseHookOutput:
    hook_event_name: Literal["PostToolUse"] = "PostToolUse"
    additional_context: Optional[str] = None
    updated_mcp_tool_output: Any = None


@dataclass
class PostToolUseFailureHookOutput:
    hook_event_name: Literal["PostToolUseFailure"] = "PostToolUseFailure"
    additional_context: Optional[str] = None


@dataclass
class PermissionDeniedHookOutput:
    hook_event_name: Literal["PermissionDenied"] = "PermissionDenied"
    retry: Optional[bool] = None


@dataclass
class NotificationHookOutput:
    hook_event_name: Literal["Notification"] = "Notification"
    additional_context: Optional[str] = None


@dataclass
class PermissionRequestAllowDecision:
    behavior: Literal["allow"] = "allow"
    updated_input: Optional[Dict[str, Any]] = None
    updated_permissions: Optional[List[PermissionUpdate]] = None


@dataclass
class PermissionRequestDenyDecision:
    behavior: Literal["deny"] = "deny"
    message: Optional[str] = None
    interrupt: Optional[bool] = None


@dataclass
class PermissionRequestHookOutput:
    hook_event_name: Literal["PermissionRequest"] = "PermissionRequest"
    decision: Union[PermissionRequestAllowDecision, PermissionRequestDenyDecision, None] = None


@dataclass
class ElicitationHookOutput:
    hook_event_name: Literal["Elicitation"] = "Elicitation"
    action: Optional[Literal["accept", "decline", "cancel"]] = None
    content: Optional[Dict[str, Any]] = None


@dataclass
class ElicitationResultHookOutput:
    hook_event_name: Literal["ElicitationResult"] = "ElicitationResult"
    action: Optional[Literal["accept", "decline", "cancel"]] = None
    content: Optional[Dict[str, Any]] = None


@dataclass
class CwdChangedHookOutput:
    hook_event_name: Literal["CwdChanged"] = "CwdChanged"
    watch_paths: Optional[List[str]] = None


@dataclass
class FileChangedHookOutput:
    hook_event_name: Literal["FileChanged"] = "FileChanged"
    watch_paths: Optional[List[str]] = None


@dataclass
class WorktreeCreateHookOutput:
    hook_event_name: Literal["WorktreeCreate"] = "WorktreeCreate"
    worktree_path: str = ""


HookSpecificOutput = Union[
    PreToolUseHookOutput,
    UserPromptSubmitHookOutput,
    SessionStartHookOutput,
    SetupHookOutput,
    SubagentStartHookOutput,
    PostToolUseHookOutput,
    PostToolUseFailureHookOutput,
    PermissionDeniedHookOutput,
    NotificationHookOutput,
    PermissionRequestHookOutput,
    ElicitationHookOutput,
    ElicitationResultHookOutput,
    CwdChangedHookOutput,
    FileChangedHookOutput,
    WorktreeCreateHookOutput,
]


@dataclass
class SyncHookResponse:
    continue_: Optional[bool] = None  # 'continue' is reserved in Python
    suppress_output: Optional[bool] = None
    stop_reason: Optional[str] = None
    decision: Optional[Literal["approve", "block"]] = None
    reason: Optional[str] = None
    system_message: Optional[str] = None
    hook_specific_output: Optional[HookSpecificOutput] = None


@dataclass
class AsyncHookResponse:
    async_: Literal[True] = True  # 'async' is reserved in Python
    async_timeout: Optional[float] = None


SyncHookJSONOutputType = SyncHookResponse
AsyncHookJSONOutputType = AsyncHookResponse
HookJSONOutputType = Union[SyncHookResponse, AsyncHookResponse]


# --------------------------------------------------------------------------
# Type guards
# --------------------------------------------------------------------------

def is_sync_hook_json_output(json_output: HookJSONOutputType) -> bool:
    """Check if response is synchronous."""
    return not isinstance(json_output, AsyncHookResponse)


def is_async_hook_json_output(json_output: HookJSONOutputType) -> bool:
    """Check if response is asynchronous."""
    return isinstance(json_output, AsyncHookResponse)


# --------------------------------------------------------------------------
# HookCallbackContext
# --------------------------------------------------------------------------

@dataclass
class HookCallbackContext:
    """Context passed to callback hooks for state access."""
    get_app_state: Callable[[], AppState] = None
    update_attribution_state: Callable = None


# --------------------------------------------------------------------------
# HookCallback
# --------------------------------------------------------------------------

@dataclass
class HookCallback:
    """Hook that is a callback."""
    type: Literal["callback"] = "callback"
    callback: Callable[..., Awaitable[HookJSONOutputType]] = None
    timeout: Optional[int] = None  # seconds
    internal: Optional[bool] = None


@dataclass
class HookCallbackMatcher:
    matcher: Optional[str] = None
    hooks: List[HookCallback] = field(default_factory=list)
    plugin_name: Optional[str] = None


# --------------------------------------------------------------------------
# HookProgress / HookBlockingError
# --------------------------------------------------------------------------

@dataclass
class HookProgress:
    type: Literal["hook_progress"] = "hook_progress"
    hook_event: HookEvent = ""
    hook_name: str = ""
    command: str = ""
    prompt_text: Optional[str] = None
    status_message: Optional[str] = None


@dataclass
class HookBlockingError:
    blocking_error: str = ""
    command: str = ""


# --------------------------------------------------------------------------
# PermissionRequestResult
# --------------------------------------------------------------------------

@dataclass
class PermissionRequestResultAllow:
    behavior: Literal["allow"] = "allow"
    updated_input: Optional[Dict[str, Any]] = None
    updated_permissions: Optional[List[PermissionUpdate]] = None


@dataclass
class PermissionRequestResultDeny:
    behavior: Literal["deny"] = "deny"
    message: Optional[str] = None
    interrupt: Optional[bool] = None


PermissionRequestResult = Union[PermissionRequestResultAllow, PermissionRequestResultDeny]


# --------------------------------------------------------------------------
# HookResult
# --------------------------------------------------------------------------

@dataclass
class HookResult:
    message: Optional[Message] = None
    system_message: Optional[Message] = None
    blocking_error: Optional[HookBlockingError] = None
    outcome: Literal["success", "blocking", "non_blocking_error", "cancelled"] = "success"
    prevent_continuation: Optional[bool] = None
    stop_reason: Optional[str] = None
    permission_behavior: Optional[Literal["ask", "deny", "allow", "passthrough"]] = None
    hook_permission_decision_reason: Optional[str] = None
    additional_context: Optional[str] = None
    initial_user_message: Optional[str] = None
    updated_input: Optional[Dict[str, Any]] = None
    updated_mcp_tool_output: Any = None
    permission_request_result: Optional[PermissionRequestResult] = None
    retry: Optional[bool] = None


@dataclass
class AggregatedHookResult:
    message: Optional[Message] = None
    blocking_errors: Optional[List[HookBlockingError]] = None
    prevent_continuation: Optional[bool] = None
    stop_reason: Optional[str] = None
    hook_permission_decision_reason: Optional[str] = None
    permission_behavior: Optional[PermissionResultBehavior] = None
    additional_contexts: Optional[List[str]] = None
    initial_user_message: Optional[str] = None
    updated_input: Optional[Dict[str, Any]] = None
    updated_mcp_tool_output: Any = None
    permission_request_result: Optional[PermissionRequestResult] = None
    retry: Optional[bool] = None
