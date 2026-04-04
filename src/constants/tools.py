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
TASK_OUTPUT_TOOL_NAME: str = "TaskOutput"
EXIT_PLAN_MODE_V2_TOOL_NAME: str = "ExitPlanMode"
ENTER_PLAN_MODE_TOOL_NAME: str = "EnterPlanMode"
AGENT_TOOL_NAME: str = "Agent"
ASK_USER_QUESTION_TOOL_NAME: str = "AskUserQuestion"
TASK_STOP_TOOL_NAME: str = "TaskStop"
FILE_READ_TOOL_NAME: str = "Read"
WEB_SEARCH_TOOL_NAME: str = "WebSearch"
TODO_WRITE_TOOL_NAME: str = "TodoWrite"
GREP_TOOL_NAME: str = "Grep"
WEB_FETCH_TOOL_NAME: str = "WebFetch"
GLOB_TOOL_NAME: str = "Glob"
FILE_EDIT_TOOL_NAME: str = "Edit"
FILE_WRITE_TOOL_NAME: str = "Write"
NOTEBOOK_EDIT_TOOL_NAME: str = "NotebookEdit"
SKILL_TOOL_NAME: str = "Skill"
SEND_MESSAGE_TOOL_NAME: str = "SendMessage"
TASK_CREATE_TOOL_NAME: str = "TaskCreate"
TASK_GET_TOOL_NAME: str = "TaskGet"
TASK_LIST_TOOL_NAME: str = "TaskList"
TASK_UPDATE_TOOL_NAME: str = "TaskUpdate"
TOOL_SEARCH_TOOL_NAME: str = "ToolSearch"
SYNTHETIC_OUTPUT_TOOL_NAME: str = "SyntheticOutput"
ENTER_WORKTREE_TOOL_NAME: str = "EnterWorktree"
EXIT_WORKTREE_TOOL_NAME: str = "ExitWorktree"
WORKFLOW_TOOL_NAME: str = "Workflow"
CRON_CREATE_TOOL_NAME: str = "CronCreate"
CRON_DELETE_TOOL_NAME: str = "CronDelete"
CRON_LIST_TOOL_NAME: str = "CronList"
BASH_TOOL_NAME: str = "Bash"

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
