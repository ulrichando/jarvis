"""
Agent memory management: persistent memory for agents across sessions.
Supports user, project, and local scopes.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, Optional

AgentMemoryScope = Literal["user", "project", "local"]


def _sanitize_agent_type_for_path(agent_type: str) -> str:
    """Replace colons with dashes for use as directory names."""
    return agent_type.replace(":", "-")


def _sanitize_path(path: str) -> str:
    """Sanitize a path for safe filesystem use."""
    return path.replace("/", "_").replace("\\", "_")


def _get_memory_base_dir() -> str:
    """Return the base memory directory (~/.jarvis or JARVIS_HOME)."""
    home = os.environ.get("JARVIS_HOME", os.path.expanduser("~/.jarvis"))
    return home


def _get_cwd() -> str:
    """Return current working directory."""
    return os.getcwd()


def _get_project_root() -> Optional[str]:
    """Return the project root (best-effort)."""
    return os.getcwd()


def _get_local_agent_memory_dir(dir_name: str) -> str:
    """
    Returns the local agent memory directory.
    When JARVIS_REMOTE_MEMORY_DIR is set, persists to the mount with project namespacing.
    Otherwise, uses <cwd>/.jarvis/agent-memory-local/<agentType>/.
    """
    remote_dir = os.environ.get("JARVIS_REMOTE_MEMORY_DIR")
    if remote_dir:
        project_root = _get_project_root() or _get_cwd()
        return os.path.join(
            remote_dir,
            "projects",
            _sanitize_path(project_root),
            "agent-memory-local",
            dir_name,
        ) + os.sep
    return os.path.join(_get_cwd(), ".jarvis", "agent-memory-local", dir_name) + os.sep


def get_agent_memory_dir(agent_type: str, scope: AgentMemoryScope) -> str:
    """
    Returns the agent memory directory for a given agent type and scope.
    - 'user' scope: <memoryBase>/agent-memory/<agentType>/
    - 'project' scope: <cwd>/.jarvis/agent-memory/<agentType>/
    - 'local' scope: see _get_local_agent_memory_dir()
    """
    dir_name = _sanitize_agent_type_for_path(agent_type)
    if scope == "project":
        return os.path.join(_get_cwd(), ".jarvis", "agent-memory", dir_name) + os.sep
    elif scope == "local":
        return _get_local_agent_memory_dir(dir_name)
    else:  # user
        return os.path.join(_get_memory_base_dir(), "agent-memory", dir_name) + os.sep


def is_agent_memory_path(absolute_path: str) -> bool:
    """Check if file is within an agent memory directory (any scope)."""
    normalized_path = os.path.normpath(absolute_path)
    memory_base = _get_memory_base_dir()
    cwd = _get_cwd()
    sep = os.sep

    # User scope
    if normalized_path.startswith(os.path.join(memory_base, "agent-memory") + sep):
        return True

    # Project scope
    if normalized_path.startswith(
        os.path.join(cwd, ".jarvis", "agent-memory") + sep
    ):
        return True

    # Local scope
    remote_dir = os.environ.get("JARVIS_REMOTE_MEMORY_DIR")
    if remote_dir:
        if (
            (sep + "agent-memory-local" + sep) in normalized_path
            and normalized_path.startswith(
                os.path.join(remote_dir, "projects") + sep
            )
        ):
            return True
    elif normalized_path.startswith(
        os.path.join(cwd, ".jarvis", "agent-memory-local") + sep
    ):
        return True

    return False


def get_agent_memory_entrypoint(agent_type: str, scope: AgentMemoryScope) -> str:
    """Returns the agent memory file path for a given agent type and scope."""
    return os.path.join(get_agent_memory_dir(agent_type, scope), "MEMORY.md")


def get_memory_scope_display(memory: Optional[AgentMemoryScope]) -> str:
    if memory == "user":
        return f"User ({os.path.join(_get_memory_base_dir(), 'agent-memory')}/)"
    elif memory == "project":
        return "Project (.jarvis/agent-memory/)"
    elif memory == "local":
        return f"Local ({_get_local_agent_memory_dir('...')})"
    else:
        return "None"


def load_agent_memory_prompt(agent_type: str, scope: AgentMemoryScope) -> str:
    """
    Load persistent memory for an agent with memory enabled.
    Creates the memory directory if needed and returns a prompt with memory contents.
    """
    if scope == "user":
        scope_note = (
            "- Since this memory is user-scope, keep learnings general "
            "since they apply across all projects"
        )
    elif scope == "project":
        scope_note = (
            "- Since this memory is project-scope and shared with your team "
            "via version control, tailor your memories to this project"
        )
    else:  # local
        scope_note = (
            "- Since this memory is local-scope (not checked into version control), "
            "tailor your memories to this project and machine"
        )

    memory_dir = get_agent_memory_dir(agent_type, scope)

    # Ensure directory exists
    os.makedirs(memory_dir, exist_ok=True)

    # Read memory files
    memory_content = ""
    memory_path = os.path.join(memory_dir, "MEMORY.md")
    if os.path.exists(memory_path):
        try:
            with open(memory_path, "r") as f:
                memory_content = f.read()
        except OSError:
            pass

    return (
        f"## Persistent Agent Memory\n\n"
        f"{scope_note}\n\n"
        f"Memory directory: {memory_dir}\n\n"
        f"{memory_content}"
    )
