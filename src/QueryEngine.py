"""QueryEngine - owns the query lifecycle and session state for a conversation.

Extracts the core logic from ask() into a standalone class that can be
used by both the headless/SDK path and the REPL.

One QueryEngine per conversation. Each submit_message() call starts a new
turn within the same conversation.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import (
    Any,
    AsyncGenerator,
    Callable,
    Dict,
    List,
    Optional,
    Set,
    Sequence,
)

from src.tools.Tool import (
    Tool,
    ToolPermissionContext,
    ToolUseContext,
    Tools,
    tool_matches_name,
)


@dataclass
class ThinkingConfig:
    type: str = "disabled"  # "disabled" | "adaptive" | "enabled"


@dataclass
class NonNullableUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


EMPTY_USAGE = NonNullableUsage()


@dataclass
class SDKPermissionDenial:
    tool_name: str
    tool_use_id: str
    tool_input: Any


@dataclass
class SDKStatus:
    status: str = ""


@dataclass
class FileStateCache:
    """Cache for file state tracking."""
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentDefinition:
    name: str
    type: str
    description: str = ""


@dataclass
class OrphanedPermission:
    permission_result: Any
    assistant_message: Any


@dataclass
class QueryEngineConfig:
    """Configuration for QueryEngine."""
    cwd: str = ""
    tools: Tools = field(default_factory=list)
    commands: List[Any] = field(default_factory=list)
    mcp_clients: List[Any] = field(default_factory=list)
    agents: List[AgentDefinition] = field(default_factory=list)
    can_use_tool: Optional[Callable] = None
    get_app_state: Optional[Callable] = None
    set_app_state: Optional[Callable] = None
    initial_messages: Optional[List[Any]] = None
    read_file_cache: FileStateCache = field(default_factory=FileStateCache)
    custom_system_prompt: Optional[str] = None
    append_system_prompt: Optional[str] = None
    user_specified_model: Optional[str] = None
    fallback_model: Optional[str] = None
    thinking_config: Optional[ThinkingConfig] = None
    max_turns: Optional[int] = None
    max_budget_usd: Optional[float] = None
    task_budget: Optional[Dict[str, float]] = None
    json_schema: Optional[Dict[str, Any]] = None
    verbose: bool = False
    replay_user_messages: bool = False
    handle_elicitation: Optional[Callable] = None
    include_partial_messages: bool = False
    set_sdk_status: Optional[Callable] = None
    abort_controller: Optional[Any] = None
    orphaned_permission: Optional[OrphanedPermission] = None
    snip_replay: Optional[Callable] = None


class QueryEngine:
    """Owns the query lifecycle and session state for a conversation.

    One QueryEngine per conversation. Each submit_message() call starts
    a new turn within the same conversation. State persists across turns.
    """

    def __init__(self, config: QueryEngineConfig) -> None:
        self.config = config
        self.mutable_messages: List[Any] = list(config.initial_messages or [])
        self.abort_controller = config.abort_controller
        self.permission_denials: List[SDKPermissionDenial] = []
        self.read_file_state = config.read_file_cache
        self.total_usage = NonNullableUsage()
        self._has_handled_orphaned_permission = False
        self._discovered_skill_names: Set[str] = set()
        self._loaded_nested_memory_paths: Set[str] = set()

    async def submit_message(
        self,
        prompt: Any,
        options: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[Any, None]:
        """Submit a message and yield response events.

        Args:
            prompt: The user prompt (string or content blocks)
            options: Optional dict with uuid, is_meta keys
        """
        self._discovered_skill_names.clear()
        opts = options or {}

        # In a full implementation, this would:
        # 1. Build system prompt
        # 2. Process user input
        # 3. Run the query loop
        # 4. Handle tool calls
        # 5. Yield stream events

        # Stub: yield nothing
        return
        yield  # Make this a generator  # noqa: E501

    def get_messages(self) -> List[Any]:
        """Get current conversation messages."""
        return list(self.mutable_messages)

    def get_total_usage(self) -> NonNullableUsage:
        """Get accumulated usage across all turns."""
        return self.total_usage

    def get_permission_denials(self) -> List[SDKPermissionDenial]:
        """Get all permission denials from this session."""
        return list(self.permission_denials)

    def abort(self) -> None:
        """Abort the current query."""
        if self.abort_controller and hasattr(self.abort_controller, "abort"):
            self.abort_controller.abort()
