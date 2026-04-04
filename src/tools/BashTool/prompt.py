"""
Prompt generation for the BashTool.
"""
from __future__ import annotations

from src.tools.AgentTool.constants import AGENT_TOOL_NAME
from src.tools.BashTool.toolName import BASH_TOOL_NAME
from src.tools.FileEditTool.constants import FILE_EDIT_TOOL_NAME
from src.tools.FileReadTool.prompt import FILE_READ_TOOL_NAME
from src.tools.FileWriteTool.prompt import FILE_WRITE_TOOL_NAME
from src.tools.GlobTool.prompt import GLOB_TOOL_NAME
from src.tools.GrepTool.prompt import GREP_TOOL_NAME
from src.tools.TodoWriteTool.constants import TODO_WRITE_TOOL_NAME

# Default timeout values (configurable at runtime)
DEFAULT_BASH_TIMEOUT_MS = 120_000
MAX_BASH_TIMEOUT_MS = 600_000


def get_default_timeout_ms() -> int:
    return DEFAULT_BASH_TIMEOUT_MS


def get_max_timeout_ms() -> int:
    return MAX_BASH_TIMEOUT_MS


def _prepend_bullets(items: list) -> list[str]:
    """Convert a list of items (possibly nested) into bullet-point strings."""
    result: list[str] = []
    for item in items:
        if isinstance(item, list):
            for sub in item:
                result.append(f"  - {sub}")
        else:
            result.append(f"- {item}")
    return result


def get_simple_prompt() -> str:
    """Generate the BashTool prompt description."""
    tool_preference_items = [
        f"File search: Use {GLOB_TOOL_NAME} (NOT find or ls)",
        f"Content search: Use {GREP_TOOL_NAME} (NOT grep or rg)",
        f"Read files: Use {FILE_READ_TOOL_NAME} (NOT cat/head/tail)",
        f"Edit files: Use {FILE_EDIT_TOOL_NAME} (NOT sed/awk)",
        f"Write files: Use {FILE_WRITE_TOOL_NAME} (NOT echo >/cat <<EOF)",
        "Communication: Output text directly (NOT echo/printf)",
    ]

    avoid_commands = "`find`, `grep`, `cat`, `head`, `tail`, `sed`, `awk`, or `echo`"

    multiple_commands_subitems = [
        f"If the commands are independent and can run in parallel, make multiple "
        f"{BASH_TOOL_NAME} tool calls in a single message. Example: if you need to run "
        f'"git status" and "git diff", send a single message with two {BASH_TOOL_NAME} '
        f"tool calls in parallel.",
        f"If the commands depend on each other and must run sequentially, use a single "
        f"{BASH_TOOL_NAME} call with '&&' to chain them together.",
        "Use ';' only when you need to run commands sequentially but don't care if "
        "earlier commands fail.",
        "DO NOT use newlines to separate commands (newlines are ok in quoted strings).",
    ]

    git_subitems = [
        "Prefer to create a new commit rather than amending an existing commit.",
        "Before running destructive operations (e.g., git reset --hard, git push --force, "
        "git checkout --), consider whether there is a safer alternative that achieves the "
        "same goal. Only use destructive operations when they are truly the best approach.",
        "Never skip hooks (--no-verify) or bypass signing (--no-gpg-sign, -c "
        "commit.gpgsign=false) unless the user has explicitly asked for it. If a hook "
        "fails, investigate and fix the underlying issue.",
    ]

    sleep_subitems = [
        "Do not sleep between commands that can run immediately -- just run them.",
        "If your command is long running and you would like to be notified when it "
        "finishes -- use `run_in_background`. No sleep needed.",
        "Do not retry failing commands in a sleep loop -- diagnose the root cause.",
        "If waiting for a background task you started with `run_in_background`, you will "
        "be notified when it completes -- do not poll.",
        "If you must poll an external process, use a check command (e.g. `gh run view`) "
        "rather than sleeping first.",
        "If you must sleep, keep the duration short (1-5 seconds) to avoid blocking the user.",
    ]

    background_note = (
        "You can use the `run_in_background` parameter to run the command in the "
        "background. Only use this if you don't need the result immediately and are OK "
        "being notified when the command completes later. You do not need to check the "
        "output right away - you'll be notified when it finishes. You do not need to use "
        "'&' at the end of the command when using this parameter."
    )

    instruction_items = [
        "If your command will create new directories or files, first use this tool to run "
        "`ls` to verify the parent directory exists and is the correct location.",
        'Always quote file paths that contain spaces with double quotes in your command '
        '(e.g., cd "path with spaces/file.txt")',
        "Try to maintain your current working directory throughout the session by using "
        "absolute paths and avoiding usage of `cd`. You may use `cd` if the User "
        "explicitly requests it.",
        f"You may specify an optional timeout in milliseconds (up to {get_max_timeout_ms()}ms "
        f"/ {get_max_timeout_ms() // 60000} minutes). By default, your command will timeout "
        f"after {get_default_timeout_ms()}ms ({get_default_timeout_ms() // 60000} minutes).",
        background_note,
        "When issuing multiple commands:",
        multiple_commands_subitems,
        "For git commands:",
        git_subitems,
        "Avoid unnecessary `sleep` commands:",
        sleep_subitems,
    ]

    lines = [
        "Executes a given bash command and returns its output.",
        "",
        "The working directory persists between commands, but shell state does not. "
        "The shell environment is initialized from the user's profile (bash or zsh).",
        "",
        f"IMPORTANT: Avoid using this tool to run {avoid_commands} commands, unless "
        "explicitly instructed or after you have verified that a dedicated tool cannot "
        "accomplish your task. Instead, use the appropriate dedicated tool as this will "
        "provide a much better experience for the user:",
        "",
        *_prepend_bullets(tool_preference_items),
        f"While the {BASH_TOOL_NAME} tool can do similar things, it's better to use the "
        "built-in tools as they provide a better user experience and make it easier to "
        "review tool calls and give permission.",
        "",
        "# Instructions",
        *_prepend_bullets(instruction_items),
    ]

    return "\n".join(lines)
