"""Tool type definitions and utilities for the agent tool system."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    FrozenSet,
    List,
    Literal,
    Optional,
    Sequence,
    Set,
    Tuple,
    Union,
)


@dataclass
class ToolInputJSONSchema:
    """JSON Schema for tool input."""
    type: str = "object"
    properties: Optional[Dict[str, Any]] = None


@dataclass
class ValidationResult:
    result: bool
    message: str = ""
    error_code: int = 0


@dataclass
class QueryChainTracking:
    chain_id: str
    depth: int


PermissionMode = Literal[
    "default",
    "plan",
    "autoApprove",
    "bypassPermissions",
]


@dataclass
class PermissionResult:
    behavior: Literal["allow", "deny", "ask"]
    updated_input: Optional[Dict[str, Any]] = None
    message: Optional[str] = None


@dataclass
class AdditionalWorkingDirectory:
    path: str
    description: Optional[str] = None


@dataclass
class ToolPermissionContext:
    """Permission context for tool execution."""
    mode: PermissionMode = "default"
    additional_working_directories: Dict[str, AdditionalWorkingDirectory] = field(
        default_factory=dict
    )
    always_allow_rules: Dict[str, Any] = field(default_factory=dict)
    always_deny_rules: Dict[str, Any] = field(default_factory=dict)
    always_ask_rules: Dict[str, Any] = field(default_factory=dict)
    is_bypass_permissions_mode_available: bool = False
    is_auto_mode_available: bool = False
    should_avoid_permission_prompts: bool = False
    await_automated_checks_before_dialog: bool = False
    pre_plan_mode: Optional[PermissionMode] = None


def get_empty_tool_permission_context() -> ToolPermissionContext:
    return ToolPermissionContext()


@dataclass
class ToolProgressData:
    """Base class for tool progress events."""
    type: str = ""
    tool_use_id: str = ""


@dataclass
class BashProgress(ToolProgressData):
    type: str = "bash_progress"
    output: str = ""


@dataclass
class WebSearchProgress(ToolProgressData):
    type: str = "web_search_progress"
    query: str = ""


@dataclass
class MCPProgress(ToolProgressData):
    type: str = "mcp_progress"
    server_name: str = ""
    tool_name: str = ""


@dataclass
class AgentToolProgress(ToolProgressData):
    type: str = "agent_tool_progress"
    agent_type: str = ""


@dataclass
class SkillToolProgress(ToolProgressData):
    type: str = "skill_tool_progress"
    skill_name: str = ""


@dataclass
class TaskOutputProgress(ToolProgressData):
    type: str = "task_output_progress"
    task_id: str = ""


@dataclass
class REPLToolProgress(ToolProgressData):
    type: str = "repl_tool_progress"


@dataclass
class ToolResult:
    """Result from a tool call."""
    data: Any
    new_messages: Optional[List[Any]] = None
    context_modifier: Optional[Callable] = None
    mcp_meta: Optional[Dict[str, Any]] = None


@dataclass
class ToolProgress:
    tool_use_id: str
    data: ToolProgressData


@dataclass
class ToolUseContext:
    """Context provided to tools during execution."""
    options: Dict[str, Any] = field(default_factory=dict)
    abort_controller: Any = None
    read_file_state: Any = None
    messages: List[Any] = field(default_factory=list)

    get_app_state: Optional[Callable] = None
    set_app_state: Optional[Callable] = None
    set_app_state_for_tasks: Optional[Callable] = None
    handle_elicitation: Optional[Callable] = None
    set_tool_jsx: Optional[Callable] = None
    add_notification: Optional[Callable] = None
    append_system_message: Optional[Callable] = None
    send_os_notification: Optional[Callable] = None
    set_in_progress_tool_use_ids: Optional[Callable] = None
    set_response_length: Optional[Callable] = None
    update_file_history_state: Optional[Callable] = None
    update_attribution_state: Optional[Callable] = None

    nested_memory_attachment_triggers: Optional[Set[str]] = None
    loaded_nested_memory_paths: Optional[Set[str]] = None
    dynamic_skill_dir_triggers: Optional[Set[str]] = None
    discovered_skill_names: Optional[Set[str]] = None
    user_modified: bool = False
    agent_id: Optional[str] = None
    agent_type: Optional[str] = None
    require_can_use_tool: bool = False
    query_tracking: Optional[QueryChainTracking] = None
    tool_use_id: Optional[str] = None


@dataclass
class McpInfo:
    server_name: str
    tool_name: str


@dataclass
class Tool:
    """Definition of a tool available to the agent."""
    name: str
    max_result_size_chars: int = 16000
    aliases: Optional[List[str]] = None
    search_hint: Optional[str] = None
    is_mcp: bool = False
    is_lsp: bool = False
    should_defer: bool = False
    always_load: bool = False
    strict: bool = False
    mcp_info: Optional[McpInfo] = None
    input_schema: Any = None
    input_json_schema: Optional[ToolInputJSONSchema] = None
    output_schema: Any = None

    # Methods with defaults
    def is_enabled(self) -> bool:
        return True

    def is_concurrency_safe(self, input: Any = None) -> bool:
        return False

    def is_read_only(self, input: Any = None) -> bool:
        return False

    def is_destructive(self, input: Any = None) -> bool:
        return False

    async def check_permissions(
        self, input: Any, context: ToolUseContext
    ) -> PermissionResult:
        return PermissionResult(behavior="allow", updated_input=input)

    def to_auto_classifier_input(self, input: Any = None) -> Any:
        return ""

    def user_facing_name(self, input: Any = None) -> str:
        return self.name

    async def call(
        self,
        args: Any,
        context: ToolUseContext,
        can_use_tool: Any = None,
        parent_message: Any = None,
        on_progress: Any = None,
    ) -> ToolResult:
        raise NotImplementedError

    async def description(self, input: Any, options: Any = None) -> str:
        return ""

    async def prompt(self, options: Any = None) -> str:
        return ""


# Type alias
Tools = Sequence[Tool]


def tool_matches_name(
    tool: Union[Tool, Dict[str, Any]],
    name: str,
) -> bool:
    """Check if a tool matches the given name (primary name or alias)."""
    if isinstance(tool, dict):
        tool_name = tool.get("name", "")
        aliases = tool.get("aliases", [])
    else:
        tool_name = tool.name
        aliases = tool.aliases or []
    return tool_name == name or name in aliases


def find_tool_by_name(tools: Sequence[Tool], name: str) -> Optional[Tool]:
    """Find a tool by name or alias from a list of tools."""
    for t in tools:
        if tool_matches_name(t, name):
            return t
    return None


def build_tool(**kwargs: Any) -> Tool:
    """Build a complete Tool from keyword arguments, filling defaults."""
    return Tool(**kwargs)
