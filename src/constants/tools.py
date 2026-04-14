"""Tool availability sets for agents and coordinators.

Note: The original TypeScript version imports tool name constants from
various tool modules. In this Python port, tool names are defined as
string constants inline since those modules may not exist in the Python
codebase. Update these values if the canonical tool name constants are
ported separately.
"""

import os
from typing import Optional

# Tool name constants (mirroring values from their respective TS modules)
TASK_OUTPUT_TOOL_NAME: str = "task_output"
EXIT_PLAN_MODE_V2_TOOL_NAME: str = "exit_plan_mode"
ENTER_PLAN_MODE_TOOL_NAME: str = "enter_plan_mode"
AGENT_TOOL_NAME: str = "agent"
ASK_USER_QUESTION_TOOL_NAME: str = "ask_user"
TASK_STOP_TOOL_NAME: str = "task_stop"
FILE_READ_TOOL_NAME: str = "read_file"
WEB_SEARCH_TOOL_NAME: str = "web_search"
TODO_WRITE_TOOL_NAME: str = "todo_write"
GREP_TOOL_NAME: str = "grep"
WEB_FETCH_TOOL_NAME: str = "web_fetch"
GLOB_TOOL_NAME: str = "glob"
FILE_EDIT_TOOL_NAME: str = "edit_file"
FILE_WRITE_TOOL_NAME: str = "write_file"
NOTEBOOK_EDIT_TOOL_NAME: str = "notebook_edit"
SKILL_TOOL_NAME: str = "skill"
SEND_MESSAGE_TOOL_NAME: str = "send_message"
TASK_CREATE_TOOL_NAME: str = "task_create"
TASK_GET_TOOL_NAME: str = "task_get"
TASK_LIST_TOOL_NAME: str = "task_list"
TASK_UPDATE_TOOL_NAME: str = "task_update"
TOOL_SEARCH_TOOL_NAME: str = "tool_search"
SYNTHETIC_OUTPUT_TOOL_NAME: str = "synthetic_output"
ENTER_WORKTREE_TOOL_NAME: str = "enter_worktree"
EXIT_WORKTREE_TOOL_NAME: str = "exit_worktree"
WORKFLOW_TOOL_NAME: str = "workflow"
CRON_CREATE_TOOL_NAME: str = "cron_create"
CRON_DELETE_TOOL_NAME: str = "cron_delete"
CRON_LIST_TOOL_NAME: str = "cron_list"
BASH_TOOL_NAME: str = "bash"

# Shell tool names (placeholder -- in TS this comes from shellToolUtils)
SHELL_TOOL_NAMES: list[str] = [BASH_TOOL_NAME]


def _build_all_agent_disallowed_tools() -> frozenset[str]:
    """Build the set of tools disallowed for all agents."""
    base = {
        TASK_OUTPUT_TOOL_NAME,
        EXIT_PLAN_MODE_V2_TOOL_NAME,
        ENTER_PLAN_MODE_TOOL_NAME,
        ASK_USER_QUESTION_TOOL_NAME,
        TASK_STOP_TOOL_NAME,
    }
    # Allow Agent tool for agents when user is ant (enables nested agents)
    if os.environ.get("USER_TYPE") != "ant":
        base.add(AGENT_TOOL_NAME)
    return frozenset(base)


ALL_AGENT_DISALLOWED_TOOLS: frozenset[str] = _build_all_agent_disallowed_tools()

CUSTOM_AGENT_DISALLOWED_TOOLS: frozenset[str] = frozenset(ALL_AGENT_DISALLOWED_TOOLS)

# Async Agent Tool Availability Status (Source of Truth)
ASYNC_AGENT_ALLOWED_TOOLS: frozenset[str] = frozenset([
    FILE_READ_TOOL_NAME,
    WEB_SEARCH_TOOL_NAME,
    TODO_WRITE_TOOL_NAME,
    GREP_TOOL_NAME,
    WEB_FETCH_TOOL_NAME,
    GLOB_TOOL_NAME,
    *SHELL_TOOL_NAMES,
    FILE_EDIT_TOOL_NAME,
    FILE_WRITE_TOOL_NAME,
    NOTEBOOK_EDIT_TOOL_NAME,
    SKILL_TOOL_NAME,
    SYNTHETIC_OUTPUT_TOOL_NAME,
    TOOL_SEARCH_TOOL_NAME,
    ENTER_WORKTREE_TOOL_NAME,
    EXIT_WORKTREE_TOOL_NAME,
])

# Tools allowed only for in-process teammates (not general async agents).
IN_PROCESS_TEAMMATE_ALLOWED_TOOLS: frozenset[str] = frozenset([
    TASK_CREATE_TOOL_NAME,
    TASK_GET_TOOL_NAME,
    TASK_LIST_TOOL_NAME,
    TASK_UPDATE_TOOL_NAME,
    SEND_MESSAGE_TOOL_NAME,
])

# Tools allowed in coordinator mode - only output and agent management tools
COORDINATOR_MODE_ALLOWED_TOOLS: frozenset[str] = frozenset([
    AGENT_TOOL_NAME,
    TASK_STOP_TOOL_NAME,
    SEND_MESSAGE_TOOL_NAME,
    SYNTHETIC_OUTPUT_TOOL_NAME,
])
