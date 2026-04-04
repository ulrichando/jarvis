"""Bridge type definitions for Remote Control sessions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Literal, Optional, Protocol

DEFAULT_SESSION_TIMEOUT_MS = 24 * 60 * 60 * 1000

BRIDGE_LOGIN_INSTRUCTION = (
    "Remote access requires a valid JARVIS auth token. "
    "Use `/login` or configure a token in ~/.jarvis/settings.json."
)

BRIDGE_LOGIN_ERROR = (
    "Error: Authentication required for remote access.\n\n"
    + BRIDGE_LOGIN_INSTRUCTION
)

REMOTE_CONTROL_DISCONNECTED_MSG = "Remote Control disconnected."

SessionDoneStatus = Literal["completed", "failed", "interrupted"]
SessionActivityType = Literal["tool_start", "text", "result", "error"]
SpawnMode = Literal["single-session", "worktree", "same-dir"]
BridgeWorkerType = Literal["claude_code", "claude_code_assistant"]


@dataclass
class WorkData:
    type: Literal["session", "healthcheck"]
    id: str


@dataclass
class WorkResponse:
    id: str
    type: str
    environment_id: str
    state: str
    data: WorkData
    secret: str
    created_at: str


@dataclass
class WorkSecret:
    version: int
    session_ingress_token: str
    api_base_url: str
    sources: list[dict[str, Any]] = field(default_factory=list)
    auth: list[dict[str, str]] = field(default_factory=list)
    claude_code_args: Optional[dict[str, str]] = None
    mcp_config: Any = None
    environment_variables: Optional[dict[str, str]] = None
    use_code_sessions: Optional[bool] = None


@dataclass
class SessionActivity:
    type: SessionActivityType
    summary: str
    timestamp: float


@dataclass
class PermissionResponseEvent:
    type: str = "control_response"
    response: Optional[dict[str, Any]] = None


@dataclass
class BridgeConfig:
    dir: str
    machine_name: str
    branch: str
    git_repo_url: Optional[str]
    max_sessions: int
    spawn_mode: SpawnMode
    verbose: bool
    sandbox: bool
    bridge_id: str
    worker_type: str
    environment_id: str
    api_base_url: str
    session_ingress_url: str
    reuse_environment_id: Optional[str] = None
    debug_file: Optional[str] = None
    session_timeout_ms: Optional[int] = None


class BridgeApiClient(Protocol):
    async def register_bridge_environment(self, config: BridgeConfig) -> dict[str, str]: ...
    async def poll_for_work(
        self, environment_id: str, environment_secret: str,
        signal: Optional[Any] = None, reclaim_older_than_ms: Optional[int] = None,
    ) -> Optional[WorkResponse]: ...
    async def acknowledge_work(self, environment_id: str, work_id: str, session_token: str) -> None: ...
    async def stop_work(self, environment_id: str, work_id: str, force: bool) -> None: ...
    async def deregister_environment(self, environment_id: str) -> None: ...
    async def send_permission_response_event(
        self, session_id: str, event: PermissionResponseEvent, session_token: str,
    ) -> None: ...
    async def archive_session(self, session_id: str) -> None: ...
    async def reconnect_session(self, environment_id: str, session_id: str) -> None: ...
    async def heartbeat_work(
        self, environment_id: str, work_id: str, session_token: str,
    ) -> dict[str, Any]: ...


@dataclass
class SessionHandle:
    session_id: str
    done: Any  # Future/Task
    activities: list[SessionActivity] = field(default_factory=list)
    current_activity: Optional[SessionActivity] = None
    access_token: str = ""
    last_stderr: list[str] = field(default_factory=list)
    _kill: Optional[Callable] = None
    _force_kill: Optional[Callable] = None
    _write_stdin: Optional[Callable] = None
    _update_access_token: Optional[Callable] = None

    def kill(self) -> None:
        if self._kill:
            self._kill()

    def force_kill(self) -> None:
        if self._force_kill:
            self._force_kill()

    def write_stdin(self, data: str) -> None:
        if self._write_stdin:
            self._write_stdin(data)

    def update_access_token(self, token: str) -> None:
        self.access_token = token
        if self._update_access_token:
            self._update_access_token(token)


@dataclass
class SessionSpawnOpts:
    session_id: str
    sdk_url: str
    access_token: str
    use_ccr_v2: bool = False
    worker_epoch: Optional[int] = None
    on_first_user_message: Optional[Callable[[str], None]] = None


class SessionSpawner(Protocol):
    def spawn(self, opts: SessionSpawnOpts, dir: str) -> SessionHandle: ...


class BridgeLogger(Protocol):
    def print_banner(self, config: BridgeConfig, environment_id: str) -> None: ...
    def log_session_start(self, session_id: str, prompt: str) -> None: ...
    def log_session_complete(self, session_id: str, duration_ms: int) -> None: ...
    def log_session_failed(self, session_id: str, error: str) -> None: ...
    def log_status(self, message: str) -> None: ...
    def log_verbose(self, message: str) -> None: ...
    def log_error(self, message: str) -> None: ...
    def log_reconnected(self, disconnected_ms: int) -> None: ...
    def update_idle_status(self) -> None: ...
    def update_reconnecting_status(self, delay_str: str, elapsed_str: str) -> None: ...
    def update_session_status(
        self, session_id: str, elapsed: str, activity: SessionActivity, trail: list[str],
    ) -> None: ...
    def clear_status(self) -> None: ...
    def set_repo_info(self, repo_name: str, branch: str) -> None: ...
    def set_debug_log_path(self, path: str) -> None: ...
    def set_attached(self, session_id: str) -> None: ...
    def update_failed_status(self, error: str) -> None: ...
    def toggle_qr(self) -> None: ...
    def update_session_count(self, active: int, max: int, mode: SpawnMode) -> None: ...
    def set_spawn_mode_display(self, mode: Optional[str]) -> None: ...
    def add_session(self, session_id: str, url: str) -> None: ...
    def update_session_activity(self, session_id: str, activity: SessionActivity) -> None: ...
    def set_session_title(self, session_id: str, title: str) -> None: ...
    def remove_session(self, session_id: str) -> None: ...
    def refresh_display(self) -> None: ...
