"""
Main entrypoint for Agent SDK types.

This file re-exports the public SDK API from:
- sdk/coreTypes - Common serializable types (messages, configs)
- sdk/runtimeTypes - Non-serializable types (callbacks, interfaces)

SDK builders who need control protocol types should import from
sdk/controlTypes directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import (
    Any,
    AsyncGenerator,
    AsyncIterable,
    Callable,
    Optional,
    Protocol,
)

# Re-export core types
from .sdk.coreTypes import *  # noqa: F401,F403


# ---------------------------------------------------------------------------
# Functions
# ---------------------------------------------------------------------------

@dataclass
class CallToolResult:
    content: list[dict[str, Any]]
    is_error: bool = False


@dataclass
class ToolAnnotations:
    read_only: Optional[bool] = None
    destructive: Optional[bool] = None
    open_world: Optional[bool] = None


@dataclass
class SdkMcpToolDefinition:
    name: str
    description: str
    input_schema: Any
    handler: Callable[..., Any]
    annotations: Optional[ToolAnnotations] = None
    search_hint: Optional[str] = None
    always_load: bool = False


@dataclass
class McpSdkServerConfigWithInstance:
    name: str
    instance: Any = None


@dataclass
class CreateSdkMcpServerOptions:
    name: str
    version: Optional[str] = None
    tools: Optional[list[SdkMcpToolDefinition]] = None


def tool(
    name: str,
    description: str,
    input_schema: Any,
    handler: Callable[..., Any],
    extras: Optional[dict[str, Any]] = None,
) -> SdkMcpToolDefinition:
    raise NotImplementedError("not implemented")


def create_sdk_mcp_server(
    options: CreateSdkMcpServerOptions,
) -> McpSdkServerConfigWithInstance:
    """
    Creates an MCP server instance that can be used with the SDK transport.
    If your SDK MCP calls will run longer than 60s, override
    CLAUDE_CODE_STREAM_CLOSE_TIMEOUT.
    """
    raise NotImplementedError("not implemented")


class AbortError(Exception):
    pass


# ---------------------------------------------------------------------------
# Query / Session types
# ---------------------------------------------------------------------------

@dataclass
class SDKSessionOptions:
    model: Optional[str] = None
    cwd: Optional[str] = None


@dataclass
class SDKSession:
    session_id: str


@dataclass
class ListSessionsOptions:
    dir: Optional[str] = None
    limit: Optional[int] = None
    offset: Optional[int] = None


@dataclass
class GetSessionInfoOptions:
    dir: Optional[str] = None


@dataclass
class GetSessionMessagesOptions:
    dir: Optional[str] = None
    limit: Optional[int] = None
    offset: Optional[int] = None
    include_system_messages: bool = False


@dataclass
class SessionMutationOptions:
    dir: Optional[str] = None


@dataclass
class ForkSessionOptions:
    dir: Optional[str] = None
    up_to_message_id: Optional[str] = None
    title: Optional[str] = None


@dataclass
class ForkSessionResult:
    session_id: str


class Query(Protocol):
    """Query handle returned by query()."""
    ...


class InternalQuery(Protocol):
    """Internal query handle."""
    ...


def query(
    prompt: str | AsyncIterable[Any] | None = None,
    options: Any = None,
) -> Query:
    raise NotImplementedError("query is not implemented in the SDK")


def unstable_v2_create_session(options: SDKSessionOptions) -> SDKSession:
    """V2 API - UNSTABLE. Create a persistent session for multi-turn conversations."""
    raise NotImplementedError("unstable_v2_createSession is not implemented in the SDK")


def unstable_v2_resume_session(
    session_id: str,
    options: SDKSessionOptions,
) -> SDKSession:
    """V2 API - UNSTABLE. Resume an existing session by ID."""
    raise NotImplementedError("unstable_v2_resumeSession is not implemented in the SDK")


async def unstable_v2_prompt(
    message: str,
    options: SDKSessionOptions,
) -> dict[str, Any]:
    """V2 API - UNSTABLE. One-shot convenience function for single prompts."""
    raise NotImplementedError("unstable_v2_prompt is not implemented in the SDK")


async def get_session_messages(
    session_id: str,
    options: Optional[GetSessionMessagesOptions] = None,
) -> list[dict[str, Any]]:
    """
    Reads a session's conversation messages from its JSONL transcript file.
    Returns array of messages, or empty list if session not found.
    """
    raise NotImplementedError("getSessionMessages is not implemented in the SDK")


async def list_sessions(
    options: Optional[ListSessionsOptions] = None,
) -> list[dict[str, Any]]:
    """List sessions with metadata."""
    raise NotImplementedError("listSessions is not implemented in the SDK")


async def get_session_info(
    session_id: str,
    options: Optional[GetSessionInfoOptions] = None,
) -> Optional[dict[str, Any]]:
    """Reads metadata for a single session by ID."""
    raise NotImplementedError("getSessionInfo is not implemented in the SDK")


async def rename_session(
    session_id: str,
    title: str,
    options: Optional[SessionMutationOptions] = None,
) -> None:
    """Rename a session."""
    raise NotImplementedError("renameSession is not implemented in the SDK")


async def tag_session(
    session_id: str,
    tag: Optional[str],
    options: Optional[SessionMutationOptions] = None,
) -> None:
    """Tag a session. Pass None to clear the tag."""
    raise NotImplementedError("tagSession is not implemented in the SDK")


async def fork_session(
    session_id: str,
    options: Optional[ForkSessionOptions] = None,
) -> ForkSessionResult:
    """Fork a session into a new branch with fresh UUIDs."""
    raise NotImplementedError("forkSession is not implemented in the SDK")


# ---------------------------------------------------------------------------
# Assistant daemon primitives (internal)
# ---------------------------------------------------------------------------

@dataclass
class CronTask:
    """A scheduled task from `<dir>/.claude/scheduled_tasks.json`."""
    id: str
    cron: str
    prompt: str
    created_at: float
    recurring: Optional[bool] = None


@dataclass
class CronJitterConfig:
    """Cron scheduler tuning knobs (jitter + expiry)."""
    recurring_frac: float
    recurring_cap_ms: float
    one_shot_max_ms: float
    one_shot_floor_ms: float
    one_shot_minute_mod: float
    recurring_max_age_ms: float


@dataclass
class ScheduledTaskFireEvent:
    type: str = "fire"
    task: Optional[CronTask] = None


@dataclass
class ScheduledTaskMissedEvent:
    type: str = "missed"
    tasks: list[CronTask] = field(default_factory=list)


ScheduledTaskEvent = ScheduledTaskFireEvent | ScheduledTaskMissedEvent


class ScheduledTasksHandle:
    """Handle returned by watch_scheduled_tasks()."""

    async def events(self) -> AsyncGenerator[ScheduledTaskEvent, None]:
        """Async stream of fire/missed events."""
        raise NotImplementedError
        yield  # type: ignore

    def get_next_fire_time(self) -> Optional[float]:
        """Epoch ms of the soonest scheduled fire, or None."""
        raise NotImplementedError


def watch_scheduled_tasks(
    dir: str,
    signal: Any,
    get_jitter_config: Optional[Callable[[], CronJitterConfig]] = None,
) -> ScheduledTasksHandle:
    """Watch scheduled_tasks.json and yield events as tasks fire."""
    raise NotImplementedError("not implemented")


def build_missed_task_notification(missed: list[CronTask]) -> str:
    """Format missed one-shot tasks into a prompt."""
    raise NotImplementedError("not implemented")


@dataclass
class InboundPrompt:
    """A user message typed on claude.ai, extracted from the bridge WS."""
    content: str | list[Any]
    uuid: Optional[str] = None


@dataclass
class ConnectRemoteControlOptions:
    dir: str
    get_access_token: Callable[[], Optional[str]]
    base_url: str
    org_uuid: str
    model: str
    name: Optional[str] = None
    worker_type: Optional[str] = None
    branch: Optional[str] = None
    git_repo_url: Optional[str] = None


class RemoteControlHandle:
    """Handle returned by connect_remote_control."""
    session_url: str
    environment_id: str
    bridge_session_id: str

    def write(self, msg: dict[str, Any]) -> None: ...
    def send_result(self) -> None: ...
    def send_control_request(self, req: Any) -> None: ...
    def send_control_response(self, res: Any) -> None: ...
    def send_control_cancel_request(self, request_id: str) -> None: ...

    async def inbound_prompts(self) -> AsyncGenerator[InboundPrompt, None]:
        raise NotImplementedError
        yield  # type: ignore

    async def control_requests(self) -> AsyncGenerator[Any, None]:
        raise NotImplementedError
        yield  # type: ignore

    async def permission_responses(self) -> AsyncGenerator[Any, None]:
        raise NotImplementedError
        yield  # type: ignore

    def on_state_change(
        self,
        cb: Callable[[str, Optional[str]], None],
    ) -> None: ...

    async def teardown(self) -> None: ...


async def connect_remote_control(
    opts: ConnectRemoteControlOptions,
) -> Optional[RemoteControlHandle]:
    """
    Hold a claude.ai remote-control bridge connection from a daemon process.
    Returns None on no-OAuth or registration failure.
    """
    raise NotImplementedError("not implemented")
