"""
Hook schemas extracted to break import cycles.

This file contains hook-related schema definitions that were originally
in src/utils/settings/types.py. By extracting them here, we break the
circular dependency between settings/types and plugins/schemas.

Both files now import from this shared location instead of each other.

Uses Pydantic models instead of Zod schemas.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal, Optional, Union

from pydantic import BaseModel, Field, HttpUrl


class ShellType(str, Enum):
    BASH = "bash"
    POWERSHELL = "powershell"


class HookEvent(str, Enum):
    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    STOP = "Stop"
    NOTIFICATION = "Notification"
    SUB_AGENT_STOP = "SubAgentStop"


HOOK_EVENTS: list[str] = [e.value for e in HookEvent]


class BashCommandHook(BaseModel):
    """Shell command hook type."""

    type: Literal["command"] = Field(description="Shell command hook type")
    command: str = Field(description="Shell command to execute")
    if_condition: Optional[str] = Field(
        default=None,
        alias="if",
        description=(
            'Permission rule syntax to filter when this hook runs (e.g., "Bash(git *)"). '
            "Only runs if the tool call matches the pattern. "
            "Avoids spawning hooks for non-matching commands."
        ),
    )
    shell: Optional[ShellType] = Field(
        default=None,
        description=(
            "Shell interpreter. 'bash' uses your $SHELL (bash/zsh/sh); "
            "'powershell' uses pwsh. Defaults to bash."
        ),
    )
    timeout: Optional[float] = Field(
        default=None,
        gt=0,
        description="Timeout in seconds for this specific command",
    )
    status_message: Optional[str] = Field(
        default=None,
        alias="statusMessage",
        description="Custom status message to display in spinner while hook runs",
    )
    once: Optional[bool] = Field(
        default=None,
        description="If true, hook runs once and is removed after execution",
    )
    async_: Optional[bool] = Field(
        default=None,
        alias="async",
        description="If true, hook runs in background without blocking",
    )
    async_rewake: Optional[bool] = Field(
        default=None,
        alias="asyncRewake",
        description=(
            "If true, hook runs in background and wakes the model on exit code 2 "
            "(blocking error). Implies async."
        ),
    )

    model_config = {"populate_by_name": True}


class PromptHook(BaseModel):
    """LLM prompt hook type."""

    type: Literal["prompt"] = Field(description="LLM prompt hook type")
    prompt: str = Field(
        description=(
            "Prompt to evaluate with LLM. "
            "Use $ARGUMENTS placeholder for hook input JSON."
        ),
    )
    if_condition: Optional[str] = Field(
        default=None,
        alias="if",
        description=(
            'Permission rule syntax to filter when this hook runs (e.g., "Bash(git *)"). '
            "Only runs if the tool call matches the pattern."
        ),
    )
    timeout: Optional[float] = Field(
        default=None,
        gt=0,
        description="Timeout in seconds for this specific prompt evaluation",
    )
    model: Optional[str] = Field(
        default=None,
        description=(
            'Model to use for this prompt hook (e.g., "claude-sonnet-4-6"). '
            "If not specified, uses the default small fast model."
        ),
    )
    status_message: Optional[str] = Field(
        default=None,
        alias="statusMessage",
        description="Custom status message to display in spinner while hook runs",
    )
    once: Optional[bool] = Field(
        default=None,
        description="If true, hook runs once and is removed after execution",
    )

    model_config = {"populate_by_name": True}


class HttpHook(BaseModel):
    """HTTP hook type."""

    type: Literal["http"] = Field(description="HTTP hook type")
    url: HttpUrl = Field(description="URL to POST the hook input JSON to")
    if_condition: Optional[str] = Field(
        default=None,
        alias="if",
        description=(
            'Permission rule syntax to filter when this hook runs (e.g., "Bash(git *)"). '
            "Only runs if the tool call matches the pattern."
        ),
    )
    timeout: Optional[float] = Field(
        default=None,
        gt=0,
        description="Timeout in seconds for this specific request",
    )
    headers: Optional[dict[str, str]] = Field(
        default=None,
        description=(
            "Additional headers to include in the request. "
            "Values may reference environment variables using $VAR_NAME or "
            '${VAR_NAME} syntax (e.g., "Authorization": "Bearer $MY_TOKEN"). '
            "Only variables listed in allowed_env_vars will be interpolated."
        ),
    )
    allowed_env_vars: Optional[list[str]] = Field(
        default=None,
        alias="allowedEnvVars",
        description=(
            "Explicit list of environment variable names that may be interpolated "
            "in header values. Only variables listed here will be resolved; "
            "all other $VAR references are left as empty strings. "
            "Required for env var interpolation to work."
        ),
    )
    status_message: Optional[str] = Field(
        default=None,
        alias="statusMessage",
        description="Custom status message to display in spinner while hook runs",
    )
    once: Optional[bool] = Field(
        default=None,
        description="If true, hook runs once and is removed after execution",
    )

    model_config = {"populate_by_name": True}


class AgentHook(BaseModel):
    """Agentic verifier hook type."""

    type: Literal["agent"] = Field(description="Agentic verifier hook type")
    prompt: str = Field(
        description=(
            'Prompt describing what to verify (e.g. "Verify that unit tests ran '
            'and passed."). Use $ARGUMENTS placeholder for hook input JSON.'
        ),
    )
    if_condition: Optional[str] = Field(
        default=None,
        alias="if",
        description=(
            'Permission rule syntax to filter when this hook runs (e.g., "Bash(git *)"). '
            "Only runs if the tool call matches the pattern."
        ),
    )
    timeout: Optional[float] = Field(
        default=None,
        gt=0,
        description="Timeout in seconds for agent execution (default 60)",
    )
    model: Optional[str] = Field(
        default=None,
        description=(
            'Model to use for this agent hook (e.g., "claude-sonnet-4-6"). '
            "If not specified, uses Haiku."
        ),
    )
    status_message: Optional[str] = Field(
        default=None,
        alias="statusMessage",
        description="Custom status message to display in spinner while hook runs",
    )
    once: Optional[bool] = Field(
        default=None,
        description="If true, hook runs once and is removed after execution",
    )

    model_config = {"populate_by_name": True}


# Union of all hook command types
HookCommand = Union[BashCommandHook, PromptHook, HttpHook, AgentHook]


class HookMatcher(BaseModel):
    """Schema for matcher configuration with multiple hooks."""

    matcher: Optional[str] = Field(
        default=None,
        description='String pattern to match (e.g. tool names like "Write")',
    )
    hooks: list[HookCommand] = Field(
        description="List of hooks to execute when the matcher matches",
    )


# Schema for hooks configuration.
# The key is the hook event. The value is an array of matcher configurations.
HooksSettings = dict[str, list[HookMatcher]]
