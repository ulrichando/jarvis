"""
Python equivalent of command.ts

Converted from TypeScript interfaces/types to Python dataclasses and TypedDicts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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


# --- Forward references (external types represented as Any or simple aliases) ---
ContentBlockParam = Any  # from @anthropic-ai/sdk
UUID = str
CanUseToolFn = Any
CompactionResult = Any
ScopedMcpServerConfig = Any
ToolUseContext = Any
EffortValue = Any
IDEExtensionInstallationStatus = Any
IdeType = Any
SettingSource = Any
HooksSettings = Any
ThemeName = Any
Message = Any
PluginManifest = Any
LogOption = Any  # defined in logs.py but referenced here for type alias


# --------------------------------------------------------------------------
# LocalCommandResult
# --------------------------------------------------------------------------

@dataclass
class LocalCommandResultText:
    type: Literal["text"] = "text"
    value: str = ""


@dataclass
class LocalCommandResultCompact:
    type: Literal["compact"] = "compact"
    compaction_result: CompactionResult = None
    display_text: Optional[str] = None


@dataclass
class LocalCommandResultSkip:
    type: Literal["skip"] = "skip"


LocalCommandResult = Union[
    LocalCommandResultText,
    LocalCommandResultCompact,
    LocalCommandResultSkip,
]


# --------------------------------------------------------------------------
# PromptCommand
# --------------------------------------------------------------------------

@dataclass
class PluginInfo:
    plugin_manifest: PluginManifest = None
    repository: str = ""


@dataclass
class PromptCommand:
    type: Literal["prompt"] = "prompt"
    progress_message: str = ""
    content_length: int = 0
    arg_names: Optional[List[str]] = None
    allowed_tools: Optional[List[str]] = None
    model: Optional[str] = None
    source: Union[SettingSource, Literal["builtin", "mcp", "plugin", "bundled"]] = "builtin"
    plugin_info: Optional[PluginInfo] = None
    disable_non_interactive: Optional[bool] = None
    hooks: Optional[HooksSettings] = None
    skill_root: Optional[str] = None
    context: Optional[Literal["inline", "fork"]] = None
    agent: Optional[str] = None
    effort: Optional[EffortValue] = None
    paths: Optional[List[str]] = None

    async def get_prompt_for_command(
        self, args: str, context: ToolUseContext
    ) -> List[ContentBlockParam]:
        raise NotImplementedError


# --------------------------------------------------------------------------
# LocalCommand types
# --------------------------------------------------------------------------

# Callback signatures
LocalCommandCall = Callable[..., Awaitable[LocalCommandResult]]
# args: str, context: LocalJSXCommandContext -> Promise<LocalCommandResult>


@dataclass
class LocalCommandModule:
    call: LocalCommandCall = None


@dataclass
class LocalCommand:
    type: Literal["local"] = "local"
    supports_non_interactive: bool = False
    load: Callable[[], Awaitable[LocalCommandModule]] = None


# --------------------------------------------------------------------------
# Resume / Display
# --------------------------------------------------------------------------

ResumeEntrypoint = Literal[
    "cli_flag",
    "slash_command_picker",
    "slash_command_session_id",
    "slash_command_title",
    "fork",
]

CommandResultDisplay = Literal["skip", "system", "user"]


# --------------------------------------------------------------------------
# LocalJSXCommandContext
# --------------------------------------------------------------------------

@dataclass
class LocalJSXCommandOptions:
    dynamic_mcp_config: Optional[Dict[str, ScopedMcpServerConfig]] = None
    ide_installation_status: Optional[IDEExtensionInstallationStatus] = None
    theme: ThemeName = None


@dataclass
class LocalJSXCommandContext:
    """ToolUseContext extended with JSX command-specific fields."""
    can_use_tool: Optional[CanUseToolFn] = None
    set_messages: Callable = None
    options: LocalJSXCommandOptions = field(default_factory=LocalJSXCommandOptions)
    on_change_api_key: Callable = None
    on_change_dynamic_mcp_config: Optional[Callable] = None
    on_install_ide_extension: Optional[Callable] = None
    resume: Optional[Callable[..., Awaitable[None]]] = None


# --------------------------------------------------------------------------
# LocalJSXCommandOnDone
# --------------------------------------------------------------------------

@dataclass
class LocalJSXCommandOnDoneOptions:
    display: Optional[CommandResultDisplay] = None
    should_query: Optional[bool] = None
    meta_messages: Optional[List[str]] = None
    next_input: Optional[str] = None
    submit_next_input: Optional[bool] = None


LocalJSXCommandOnDone = Callable[..., None]
# (result?: str, options?: LocalJSXCommandOnDoneOptions) -> None

LocalJSXCommandCall = Callable[..., Awaitable[Any]]
# (on_done, context, args) -> Promise<ReactNode>


@dataclass
class LocalJSXCommandModule:
    call: LocalJSXCommandCall = None


@dataclass
class LocalJSXCommand:
    type: Literal["local-jsx"] = "local-jsx"
    load: Callable[[], Awaitable[LocalJSXCommandModule]] = None


# --------------------------------------------------------------------------
# CommandAvailability
# --------------------------------------------------------------------------

CommandAvailability = Literal["claude-ai", "console"]


# --------------------------------------------------------------------------
# CommandBase
# --------------------------------------------------------------------------

@dataclass
class CommandBase:
    name: str = ""
    description: str = ""
    availability: Optional[List[CommandAvailability]] = None
    has_user_specified_description: Optional[bool] = None
    is_enabled: Optional[Callable[[], bool]] = None
    is_hidden: Optional[bool] = None
    aliases: Optional[List[str]] = None
    is_mcp: Optional[bool] = None
    argument_hint: Optional[str] = None
    when_to_use: Optional[str] = None
    version: Optional[str] = None
    disable_model_invocation: Optional[bool] = None
    user_invocable: Optional[bool] = None
    loaded_from: Optional[
        Literal["commands_DEPRECATED", "skills", "plugin", "managed", "bundled", "mcp"]
    ] = None
    kind: Optional[Literal["workflow"]] = None
    immediate: Optional[bool] = None
    is_sensitive: Optional[bool] = None
    user_facing_name: Optional[Callable[[], str]] = None


# Command = CommandBase + (PromptCommand | LocalCommand | LocalJSXCommand)
# In Python we represent this as a union or just use CommandBase with an
# embedded variant field.

@dataclass
class Command(CommandBase):
    """A command combining CommandBase with one of PromptCommand, LocalCommand, or LocalJSXCommand."""
    variant: Union[PromptCommand, LocalCommand, LocalJSXCommand, None] = None


# --------------------------------------------------------------------------
# Helper functions
# --------------------------------------------------------------------------

def get_command_name(cmd: CommandBase) -> str:
    """Resolves the user-visible name, falling back to cmd.name when not overridden."""
    if cmd.user_facing_name is not None:
        return cmd.user_facing_name()
    return cmd.name


def is_command_enabled(cmd: CommandBase) -> bool:
    """Resolves whether the command is enabled, defaulting to True."""
    if cmd.is_enabled is not None:
        return cmd.is_enabled()
    return True
