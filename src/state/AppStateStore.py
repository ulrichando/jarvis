"""
Application state type definitions and default factory.
Python equivalent of AppStateStore.ts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Literal, Optional, Set, TypedDict, Union

from .store import Store


# ---------------------------------------------------------------------------
# Supporting types
# ---------------------------------------------------------------------------

class CompletionBoundaryComplete(TypedDict):
    type: Literal["complete"]
    completed_at: float
    output_tokens: int


class CompletionBoundaryBash(TypedDict):
    type: Literal["bash"]
    command: str
    completed_at: float


class CompletionBoundaryEdit(TypedDict):
    type: Literal["edit"]
    tool_name: str
    file_path: str
    completed_at: float


class CompletionBoundaryDenied(TypedDict):
    type: Literal["denied_tool"]
    tool_name: str
    detail: str
    completed_at: float


CompletionBoundary = Union[
    CompletionBoundaryComplete,
    CompletionBoundaryBash,
    CompletionBoundaryEdit,
    CompletionBoundaryDenied,
]


class SpeculationResultDict(TypedDict):
    messages: List[Any]  # Message[]
    boundary: Optional[CompletionBoundary]
    time_saved_ms: float


@dataclass
class SpeculationIdle:
    status: Literal["idle"] = "idle"


@dataclass
class SpeculationActive:
    status: Literal["active"] = "active"
    id: str = ""
    abort: Callable[[], None] = lambda: None
    start_time: float = 0.0
    messages_ref: List[Any] = field(default_factory=list)
    written_paths_ref: Set[str] = field(default_factory=set)
    boundary: Optional[CompletionBoundary] = None
    suggestion_length: int = 0
    tool_use_count: int = 0
    is_pipelined: bool = False
    context_ref: Any = None
    pipelined_suggestion: Optional[dict] = None


SpeculationState = Union[SpeculationIdle, SpeculationActive]

IDLE_SPECULATION_STATE: SpeculationState = SpeculationIdle()

FooterItem = Literal["tasks", "tmux", "bagel", "teams", "bridge", "companion"]


# ---------------------------------------------------------------------------
# Nested state dicts
# ---------------------------------------------------------------------------

class MCPState(TypedDict, total=False):
    clients: List[Any]
    tools: List[Any]
    commands: List[Any]
    resources: Dict[str, List[Any]]
    plugin_reconnect_key: int


class PluginInstallationEntry(TypedDict, total=False):
    name: str
    id: str
    status: Literal["pending", "installing", "installed", "failed"]
    error: Optional[str]


class PluginInstallationStatus(TypedDict):
    marketplaces: List[PluginInstallationEntry]
    plugins: List[PluginInstallationEntry]


class PluginsState(TypedDict, total=False):
    enabled: List[Any]
    disabled: List[Any]
    commands: List[Any]
    errors: List[Any]
    installation_status: PluginInstallationStatus
    needs_refresh: bool


class NotificationsState(TypedDict):
    current: Optional[Any]
    queue: List[Any]


class ElicitationState(TypedDict):
    queue: List[Any]


class PromptSuggestionState(TypedDict):
    text: Optional[str]
    prompt_id: Optional[Literal["user_intent", "stated_intent"]]
    shown_at: float
    accepted_at: float
    generation_request_id: Optional[str]


class SkillImprovementSuggestion(TypedDict):
    skill_name: str
    updates: List[Dict[str, str]]  # section, change, reason


class SkillImprovementState(TypedDict):
    suggestion: Optional[SkillImprovementSuggestion]


class InboxMessage(TypedDict, total=False):
    id: str
    from_: str  # 'from' is reserved in Python
    text: str
    timestamp: str
    status: Literal["pending", "processing", "processed"]
    color: Optional[str]
    summary: Optional[str]


class InboxState(TypedDict):
    messages: List[InboxMessage]


class SandboxPermissionRequest(TypedDict, total=False):
    request_id: str
    worker_id: str
    worker_name: str
    worker_color: Optional[str]
    host: str
    created_at: float


class WorkerSandboxPermissions(TypedDict):
    queue: List[SandboxPermissionRequest]
    selected_index: int


class PendingWorkerRequest(TypedDict):
    tool_name: str
    tool_use_id: str
    description: str


class PendingSandboxRequest(TypedDict):
    request_id: str
    host: str


class InitialMessage(TypedDict, total=False):
    message: Any  # UserMessage
    clear_context: bool
    mode: str  # PermissionMode
    allowed_prompts: List[Any]


class ToolPermissionContext(TypedDict, total=False):
    mode: str
    is_bypass_permissions_mode_available: bool
    always_allow_rules: Dict[str, List[str]]


class TeammateInfo(TypedDict, total=False):
    name: str
    agent_type: Optional[str]
    color: Optional[str]
    tmux_session_name: str
    tmux_pane_id: str
    cwd: str
    worktree_path: Optional[str]
    spawned_at: float


class TeamContext(TypedDict, total=False):
    team_name: str
    team_file_path: str
    lead_agent_id: str
    self_agent_id: Optional[str]
    self_agent_name: Optional[str]
    is_leader: Optional[bool]
    self_agent_color: Optional[str]
    teammates: Dict[str, TeammateInfo]


class TungstenSession(TypedDict, total=False):
    session_name: str
    socket_name: str
    target: str


class TungstenCommand(TypedDict):
    command: str
    timestamp: float


class ScreenshotDims(TypedDict, total=False):
    width: int
    height: int
    display_width: int
    display_height: int
    display_id: Optional[int]
    origin_x: Optional[int]
    origin_y: Optional[int]


class AppGrant(TypedDict):
    bundle_id: str
    display_name: str
    granted_at: float


class CuGrantFlags(TypedDict):
    clipboard_read: bool
    clipboard_write: bool
    system_key_combos: bool


class ComputerUseMcpState(TypedDict, total=False):
    allowed_apps: List[AppGrant]
    grant_flags: CuGrantFlags
    last_screenshot_dims: ScreenshotDims
    hidden_during_turn: Set[str]
    selected_display_id: Optional[int]
    display_pinned_by_model: bool
    display_resolved_for_apps: Optional[str]


# ---------------------------------------------------------------------------
# Main AppState
# ---------------------------------------------------------------------------

@dataclass
class AppState:
    """Central application state container."""

    settings: Dict[str, Any] = field(default_factory=dict)
    verbose: bool = False
    main_loop_model: Optional[str] = None
    main_loop_model_for_session: Optional[str] = None
    status_line_text: Optional[str] = None
    expanded_view: Literal["none", "tasks", "teammates"] = "none"
    is_brief_only: bool = False
    show_teammate_message_preview: bool = False
    selected_ip_agent_index: int = -1
    coordinator_task_index: int = -1
    view_selection_mode: Literal["none", "selecting-agent", "viewing-agent"] = "none"
    footer_selection: Optional[FooterItem] = None
    tool_permission_context: Dict[str, Any] = field(default_factory=lambda: {"mode": "default"})
    spinner_tip: Optional[str] = None
    agent: Optional[str] = None
    kairos_enabled: bool = False
    remote_session_url: Optional[str] = None
    remote_connection_status: Literal[
        "connecting", "connected", "reconnecting", "disconnected"
    ] = "connecting"
    remote_background_task_count: int = 0

    # Bridge state
    repl_bridge_enabled: bool = False
    repl_bridge_explicit: bool = False
    repl_bridge_outbound_only: bool = False
    repl_bridge_connected: bool = False
    repl_bridge_session_active: bool = False
    repl_bridge_reconnecting: bool = False
    repl_bridge_connect_url: Optional[str] = None
    repl_bridge_session_url: Optional[str] = None
    repl_bridge_environment_id: Optional[str] = None
    repl_bridge_session_id: Optional[str] = None
    repl_bridge_error: Optional[str] = None
    repl_bridge_initial_name: Optional[str] = None
    show_remote_callout: bool = False

    # Mutable complex state
    tasks: Dict[str, Any] = field(default_factory=dict)
    agent_name_registry: Dict[str, str] = field(default_factory=dict)
    foregrounded_task_id: Optional[str] = None
    viewing_agent_task_id: Optional[str] = None
    companion_reaction: Optional[str] = None
    companion_pet_at: Optional[float] = None

    mcp: Dict[str, Any] = field(default_factory=lambda: {
        "clients": [],
        "tools": [],
        "commands": [],
        "resources": {},
        "plugin_reconnect_key": 0,
    })
    plugins: Dict[str, Any] = field(default_factory=lambda: {
        "enabled": [],
        "disabled": [],
        "commands": [],
        "errors": [],
        "installation_status": {"marketplaces": [], "plugins": []},
        "needs_refresh": False,
    })
    agent_definitions: Dict[str, List[Any]] = field(
        default_factory=lambda: {"active_agents": [], "all_agents": []}
    )
    file_history: Dict[str, Any] = field(default_factory=lambda: {
        "snapshots": [],
        "tracked_files": set(),
        "snapshot_sequence": 0,
    })
    attribution: Dict[str, Any] = field(default_factory=dict)
    todos: Dict[str, Any] = field(default_factory=dict)
    remote_agent_task_suggestions: List[Dict[str, str]] = field(default_factory=list)
    notifications: Dict[str, Any] = field(
        default_factory=lambda: {"current": None, "queue": []}
    )
    elicitation: Dict[str, List[Any]] = field(
        default_factory=lambda: {"queue": []}
    )
    thinking_enabled: Optional[bool] = None
    prompt_suggestion_enabled: bool = False
    session_hooks: Dict[str, Any] = field(default_factory=dict)

    # Tungsten / tmux
    tungsten_active_session: Optional[Dict[str, str]] = None
    tungsten_last_captured_time: Optional[float] = None
    tungsten_last_command: Optional[Dict[str, Any]] = None
    tungsten_panel_visible: Optional[bool] = None
    tungsten_panel_auto_hidden: Optional[bool] = None

    # WebBrowser (bagel)
    bagel_active: Optional[bool] = None
    bagel_url: Optional[str] = None
    bagel_panel_visible: Optional[bool] = None

    # Computer-use MCP
    computer_use_mcp_state: Optional[Dict[str, Any]] = None

    # REPL context
    repl_context: Optional[Dict[str, Any]] = None

    # Team context
    team_context: Optional[Dict[str, Any]] = None
    standalone_agent_context: Optional[Dict[str, Any]] = None

    # Inbox
    inbox: Dict[str, List[Any]] = field(
        default_factory=lambda: {"messages": []}
    )
    worker_sandbox_permissions: Dict[str, Any] = field(
        default_factory=lambda: {"queue": [], "selected_index": 0}
    )
    pending_worker_request: Optional[Dict[str, str]] = None
    pending_sandbox_request: Optional[Dict[str, str]] = None

    # Prompt suggestion
    prompt_suggestion: Dict[str, Any] = field(default_factory=lambda: {
        "text": None,
        "prompt_id": None,
        "shown_at": 0,
        "accepted_at": 0,
        "generation_request_id": None,
    })

    speculation: SpeculationState = field(default_factory=SpeculationIdle)
    speculation_session_time_saved_ms: float = 0.0

    skill_improvement: Dict[str, Any] = field(
        default_factory=lambda: {"suggestion": None}
    )
    auth_version: int = 0
    initial_message: Optional[Dict[str, Any]] = None

    # Plan verification
    pending_plan_verification: Optional[Dict[str, Any]] = None
    denial_tracking: Optional[Dict[str, Any]] = None
    active_overlays: Set[str] = field(default_factory=set)
    fast_mode: bool = False
    advisor_model: Optional[str] = None
    effort_value: Optional[Any] = None

    # Ultraplan
    ultraplan_launching: Optional[bool] = None
    ultraplan_session_url: Optional[str] = None
    ultraplan_pending_choice: Optional[Dict[str, str]] = None
    ultraplan_launch_pending: Optional[Dict[str, str]] = None
    is_ultraplan_mode: Optional[bool] = None

    # Bridge permission callbacks (runtime, not serializable)
    repl_bridge_permission_callbacks: Optional[Any] = None
    channel_permission_callbacks: Optional[Any] = None


AppStateStore = Store[AppState]


def get_default_app_state() -> AppState:
    """Return a fresh default AppState instance."""
    return AppState(
        thinking_enabled=True,
        prompt_suggestion_enabled=False,
    )
