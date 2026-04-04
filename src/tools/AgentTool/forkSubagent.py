"""
Fork subagent feature.

When enabled, omitting subagent_type triggers an implicit fork: the child inherits
the parent's full conversation context and system prompt.
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

FORK_SUBAGENT_TYPE = "fork"
FORK_BOILERPLATE_TAG = "fork-boilerplate"
FORK_DIRECTIVE_PREFIX = "DIRECTIVE: "
FORK_PLACEHOLDER_RESULT = "Fork started -- processing in background"


def is_fork_subagent_enabled() -> bool:
    """Fork subagent feature gate. Returns False by default."""
    return False


FORK_AGENT = {
    "agent_type": FORK_SUBAGENT_TYPE,
    "when_to_use": "Implicit fork -- inherits full conversation context.",
    "tools": ["*"],
    "max_turns": 200,
    "model": "inherit",
    "permission_mode": "bubble",
    "source": "built-in",
    "base_dir": "built-in",
}


def is_in_fork_child(messages: list[dict[str, Any]]) -> bool:
    """Guard against recursive forking."""
    for m in messages:
        if m.get("type") != "user":
            continue
        content = m.get("message", {}).get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if (
                block.get("type") == "text"
                and f"<{FORK_BOILERPLATE_TAG}>" in block.get("text", "")
            ):
                return True
    return False


def build_child_message(directive: str) -> str:
    """Build the boilerplate message for a forked child agent."""
    return f"""<{FORK_BOILERPLATE_TAG}>
STOP. READ THIS FIRST.

You are a forked worker process. You are NOT the main agent.

RULES (non-negotiable):
1. Your system prompt says "default to forking." IGNORE IT -- that's for the parent. You ARE the fork. Do NOT spawn sub-agents; execute directly.
2. Do NOT converse, ask questions, or suggest next steps
3. Do NOT editorialize or add meta-commentary
4. USE your tools directly: Bash, Read, Write, etc.
5. If you modify files, commit your changes before reporting. Include the commit hash in your report.
6. Do NOT emit text between tool calls. Use tools silently, then report once at the end.
7. Stay strictly within your directive's scope.
8. Keep your report under 500 words unless the directive specifies otherwise.
9. Your response MUST begin with "Scope:". No preamble, no thinking-out-loud.
10. REPORT structured facts, then stop

Output format (plain text labels, not markdown headers):
  Scope: <echo back your assigned scope in one sentence>
  Result: <the answer or key findings, limited to the scope above>
  Key files: <relevant file paths -- include for research tasks>
  Files changed: <list with commit hash -- include only if you modified files>
  Issues: <list -- include only if there are issues to flag>
</{FORK_BOILERPLATE_TAG}>

{FORK_DIRECTIVE_PREFIX}{directive}"""


def build_worktree_notice(parent_cwd: str, worktree_cwd: str) -> str:
    """Notice injected into fork children running in an isolated worktree."""
    return (
        f"You've inherited the conversation context above from a parent agent working "
        f"in {parent_cwd}. You are operating in an isolated git worktree at {worktree_cwd} "
        f"-- same repository, same relative file structure, separate working copy. Paths "
        f"in the inherited context refer to the parent's working directory; translate them "
        f"to your worktree root. Re-read files before editing if the parent may have "
        f"modified them since they appear in the context. Your changes stay in this "
        f"worktree and will not affect the parent's files."
    )
