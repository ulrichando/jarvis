"""Agent file utilities.

Load, save, update, and delete agent definition files (.md with YAML frontmatter).
"""

from __future__ import annotations
from typing import Any, Optional
import os
import re

from .types import AgentDefinition, AGENT_PATHS


def getAgentDirectoryPath(base_path: str) -> str:
    """Get the absolute path to the agents directory.

    Args:
        base_path: Base project or home directory path.

    Returns:
        Full path to the agents directory.
    """
    return os.path.join(base_path, AGENT_PATHS["FOLDER_NAME"], AGENT_PATHS["AGENTS_DIR"])


def getRelativeAgentDirectoryPath() -> str:
    """Get the relative path to the agents directory.

    Returns:
        Relative path string.
    """
    return os.path.join(AGENT_PATHS["FOLDER_NAME"], AGENT_PATHS["AGENTS_DIR"])


def getNewAgentFilePath(base_path: str, agent_name: str) -> str:
    """Get the file path for a new agent definition.

    Args:
        base_path: Base directory.
        agent_name: Name of the agent.

    Returns:
        Full path for the agent .md file.
    """
    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", agent_name.lower())
    return os.path.join(getAgentDirectoryPath(base_path), f"{safe_name}.md")


def getActualAgentFilePath(base_path: str, agent_name: str) -> Optional[str]:
    """Find the actual file path for an existing agent.

    Searches for the agent file in the agents directory.

    Args:
        base_path: Base directory.
        agent_name: Name of the agent to find.

    Returns:
        Full path if found, None otherwise.
    """
    agents_dir = getAgentDirectoryPath(base_path)
    if not os.path.isdir(agents_dir):
        return None

    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", agent_name.lower())
    expected = os.path.join(agents_dir, f"{safe_name}.md")
    if os.path.isfile(expected):
        return expected

    # Search by name in frontmatter
    for fname in os.listdir(agents_dir):
        if not fname.endswith(".md"):
            continue
        fpath = os.path.join(agents_dir, fname)
        try:
            with open(fpath, "r") as f:
                content = f.read(500)
            if f"name: {agent_name}" in content or f'name: "{agent_name}"' in content:
                return fpath
        except OSError:
            continue

    return None


def getNewRelativeAgentFilePath(agent_name: str) -> str:
    """Get the relative file path for a new agent.

    Args:
        agent_name: Name of the agent.

    Returns:
        Relative path string.
    """
    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", agent_name.lower())
    return os.path.join(getRelativeAgentDirectoryPath(), f"{safe_name}.md")


def getActualRelativeAgentFilePath(base_path: str, agent_name: str) -> Optional[str]:
    """Get the relative path for an existing agent file.

    Args:
        base_path: Base directory.
        agent_name: Agent name.

    Returns:
        Relative path if found, None otherwise.
    """
    full_path = getActualAgentFilePath(base_path, agent_name)
    if full_path and full_path.startswith(base_path):
        return os.path.relpath(full_path, base_path)
    return None


def ensureAgentDirectoryExists(base_path: str) -> str:
    """Create the agents directory if it does not exist.

    Args:
        base_path: Base directory.

    Returns:
        Path to the agents directory.
    """
    agents_dir = getAgentDirectoryPath(base_path)
    os.makedirs(agents_dir, exist_ok=True)
    return agents_dir


def _serialize_agent(agent: AgentDefinition) -> str:
    """Serialize an agent definition to markdown with YAML frontmatter.

    Args:
        agent: Agent definition to serialize.

    Returns:
        Markdown string with YAML frontmatter.
    """
    frontmatter_lines = [
        "---",
        f"name: {agent.name}",
        f"type: {agent.agent_type}",
    ]
    if agent.description:
        frontmatter_lines.append(f"description: {agent.description}")
    if agent.model:
        frontmatter_lines.append(f"model: {agent.model}")
    if agent.tools:
        frontmatter_lines.append(f"tools: [{', '.join(agent.tools)}]")
    if agent.color:
        frontmatter_lines.append(f"color: {agent.color}")
    if agent.sub_agents:
        frontmatter_lines.append(f"sub_agents: [{', '.join(agent.sub_agents)}]")
    frontmatter_lines.append("---")
    frontmatter_lines.append("")

    if agent.prompt:
        frontmatter_lines.append(agent.prompt)

    return "\n".join(frontmatter_lines)


def _parse_agent(content: str, source: str = "") -> AgentDefinition:
    """Parse an agent definition from markdown with YAML frontmatter.

    Args:
        content: File content string.
        source: Source file path.

    Returns:
        Parsed AgentDefinition.
    """
    agent = AgentDefinition(source=source)

    # Extract frontmatter
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)", content, re.DOTALL)
    if not match:
        agent.prompt = content
        return agent

    frontmatter = match.group(1)
    body = match.group(2).strip()
    agent.prompt = body

    for line in frontmatter.split("\n"):
        line = line.strip()
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip().strip('"').strip("'")

        if key == "name":
            agent.name = value
        elif key == "type":
            agent.agent_type = value
        elif key == "description":
            agent.description = value
        elif key == "model":
            agent.model = value
        elif key == "color":
            agent.color = value
        elif key == "tools":
            # Parse [tool1, tool2] format
            tools_str = value.strip("[]")
            agent.tools = [t.strip() for t in tools_str.split(",") if t.strip()]
        elif key == "sub_agents":
            agents_str = value.strip("[]")
            agent.sub_agents = [a.strip() for a in agents_str.split(",") if a.strip()]

    return agent


def saveAgentToFile(base_path: str, agent: AgentDefinition) -> str:
    """Save a new agent definition to a file.

    Args:
        base_path: Base directory.
        agent: Agent definition to save.

    Returns:
        Path to the saved file.
    """
    ensureAgentDirectoryExists(base_path)
    file_path = getNewAgentFilePath(base_path, agent.name)
    content = _serialize_agent(agent)
    writeFileAndFlush(file_path, content)
    return file_path


def updateAgentFile(file_path: str, agent: AgentDefinition) -> None:
    """Update an existing agent file.

    Args:
        file_path: Path to the agent file.
        agent: Updated agent definition.
    """
    content = _serialize_agent(agent)
    writeFileAndFlush(file_path, content)


def deleteAgentFromFile(file_path: str) -> bool:
    """Delete an agent definition file.

    Args:
        file_path: Path to the agent file to delete.

    Returns:
        True if the file was deleted.
    """
    try:
        os.remove(file_path)
        return True
    except OSError:
        return False


def writeFileAndFlush(file_path: str, content: str) -> None:
    """Write content to a file and flush to disk.

    Args:
        file_path: Target file path.
        content: Content to write.
    """
    with open(file_path, "w") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())


def loadAgentFromFile(file_path: str) -> Optional[AgentDefinition]:
    """Load an agent definition from a file.

    Args:
        file_path: Path to the agent .md file.

    Returns:
        AgentDefinition if successful, None on error.
    """
    try:
        with open(file_path, "r") as f:
            content = f.read()
        return _parse_agent(content, source=file_path)
    except OSError:
        return None


def loadAllAgents(base_path: str) -> list[AgentDefinition]:
    """Load all agent definitions from the agents directory.

    Args:
        base_path: Base directory.

    Returns:
        List of loaded AgentDefinition objects.
    """
    agents_dir = getAgentDirectoryPath(base_path)
    if not os.path.isdir(agents_dir):
        return []

    agents = []
    for fname in sorted(os.listdir(agents_dir)):
        if not fname.endswith(".md"):
            continue
        fpath = os.path.join(agents_dir, fname)
        agent = loadAgentFromFile(fpath)
        if agent:
            agents.append(agent)
    return agents
