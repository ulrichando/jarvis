"""XML tag names used in messages and command metadata."""

from typing import List, Tuple

# XML tag names for skill/command metadata
COMMAND_NAME_TAG = "command-name"
COMMAND_MESSAGE_TAG = "command-message"
COMMAND_ARGS_TAG = "command-args"

# XML tag names for terminal/bash command input and output
BASH_INPUT_TAG = "bash-input"
BASH_STDOUT_TAG = "bash-stdout"
BASH_STDERR_TAG = "bash-stderr"
LOCAL_COMMAND_STDOUT_TAG = "local-command-stdout"
LOCAL_COMMAND_STDERR_TAG = "local-command-stderr"
LOCAL_COMMAND_CAVEAT_TAG = "local-command-caveat"

# All terminal-related tags
TERMINAL_OUTPUT_TAGS: Tuple[str, ...] = (
    BASH_INPUT_TAG,
    BASH_STDOUT_TAG,
    BASH_STDERR_TAG,
    LOCAL_COMMAND_STDOUT_TAG,
    LOCAL_COMMAND_STDERR_TAG,
    LOCAL_COMMAND_CAVEAT_TAG,
)

TICK_TAG = "tick"

# XML tag names for task notifications
TASK_NOTIFICATION_TAG = "task-notification"
TASK_ID_TAG = "task-id"
TOOL_USE_ID_TAG = "tool-use-id"
TASK_TYPE_TAG = "task-type"
OUTPUT_FILE_TAG = "output-file"
STATUS_TAG = "status"
SUMMARY_TAG = "summary"
REASON_TAG = "reason"
WORKTREE_TAG = "worktree"
WORKTREE_PATH_TAG = "worktreePath"
WORKTREE_BRANCH_TAG = "worktreeBranch"

# XML tag names for ultraplan mode
ULTRAPLAN_TAG = "ultraplan"

# XML tag name for remote /review results
REMOTE_REVIEW_TAG = "remote-review"
REMOTE_REVIEW_PROGRESS_TAG = "remote-review-progress"

# XML tag name for teammate messages
TEAMMATE_MESSAGE_TAG = "teammate-message"

# XML tag name for external channel messages
CHANNEL_MESSAGE_TAG = "channel-message"
CHANNEL_TAG = "channel"

# XML tag name for cross-session UDS messages
CROSS_SESSION_MESSAGE_TAG = "cross-session-message"

# Fork boilerplate tags
FORK_BOILERPLATE_TAG = "fork-boilerplate"
FORK_DIRECTIVE_PREFIX = "Your directive: "

# Common argument patterns for slash commands
COMMON_HELP_ARGS: List[str] = ["help", "-h", "--help"]

COMMON_INFO_ARGS: List[str] = [
    "list",
    "show",
    "display",
    "current",
    "view",
    "get",
    "check",
    "describe",
    "print",
    "version",
    "about",
    "status",
    "?",
]
