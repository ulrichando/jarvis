"""JARVIS Agent Registry — discover, load, create, and manage custom agents.

Custom agents are markdown files with YAML frontmatter, stored in:
  - ~/.jarvis/agents/     (user-scope, available across all projects)
  - .jarvis/agents/       (project-scope, project-specific)

Format mirrors Claude Code's agent system:
  ---
  name: Agent Name
  description: One-line description for auto-matching
  model: optional model preference
  allowed_tools: [bash, read_file, ...]
  max_iterations: 15
  ---
  System prompt goes here.
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from brain.config import JARVIS_HOME

log = logging.getLogger("jarvis.agent.registry")

# Agent directories: user-global and project-local
AGENT_DIRS = [
    JARVIS_HOME / "agents",
    Path(".jarvis") / "agents",
]

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

# All known tools the agent loop can execute
ALL_TOOLS = [
    "bash", "read_file", "write_file", "edit_file",
    "search_files", "web_search", "web_fetch", "web_api",
    "think", "dispatch", "view_screen",
]

# Presets for quick tool selection
TOOL_PRESETS = {
    "read-only": ["read_file", "search_files", "bash", "think"],
    "full": ["bash", "read_file", "write_file", "edit_file",
             "search_files", "web_search", "web_fetch", "think"],
    "analyst": ["read_file", "search_files", "web_search", "web_fetch", "think"],
}


def _parse_yaml_lite(text: str) -> dict:
    """Minimal YAML parser for agent frontmatter (no PyYAML dependency)."""
    result: dict = {}
    lines = text.splitlines()
    current_key: Optional[str] = None
    current_list: Optional[list] = None

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if stripped.startswith("- ") and current_key is not None:
            if current_list is None:
                current_list = []
                result[current_key] = current_list
            current_list.append(stripped[2:].strip().strip('"').strip("'"))
            continue

        if ":" in stripped:
            current_list = None
            key, _, value = stripped.partition(":")
            key = key.strip()
            value = value.strip()
            current_key = key

            if not value:
                result[key] = []
                current_list = result[key]
            elif value.startswith("[") and value.endswith("]"):
                items = value[1:-1].split(",")
                result[key] = [i.strip().strip('"').strip("'") for i in items if i.strip()]
            elif value.lower() in ("true", "yes"):
                result[key] = True
            elif value.lower() in ("false", "no"):
                result[key] = False
            elif value.isdigit():
                result[key] = int(value)
            else:
                result[key] = value.strip('"').strip("'")

    return result


@dataclass
class CustomAgent:
    """A custom agent loaded from a .md file."""
    name: str
    description: str = ""
    system_prompt: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    max_iterations: int = 15
    model: str = ""
    scope: str = "user"         # "user" or "project"
    path: Optional[Path] = None
    bash_readonly: bool = False  # enforce read-only bash like scout

    def to_agent_config(self):
        """Convert to AgentConfig for use in the agent loop."""
        from brain.agent.agents import AgentConfig
        return AgentConfig(
            name=self.name,
            description=self.description,
            system_prompt=self.system_prompt,
            allowed_tools=self.allowed_tools if self.allowed_tools else TOOL_PRESETS["full"],
            max_iterations=self.max_iterations,
            bash_readonly=self.bash_readonly,
        )

    def to_dict(self) -> dict:
        """Serialize to dict for display/storage."""
        return {
            "name": self.name,
            "description": self.description,
            "allowed_tools": self.allowed_tools,
            "max_iterations": self.max_iterations,
            "model": self.model,
            "scope": self.scope,
            "bash_readonly": self.bash_readonly,
            "path": str(self.path) if self.path else None,
        }


class AgentRegistry:
    """Discover, load, create, and manage custom agent definitions."""

    def __init__(self):
        self._agents: dict[str, CustomAgent] = {}

    # ── Discovery ────────────────────────────────────────────────────

    def discover(self) -> int:
        """Scan agent directories and load all .md agent files.

        Returns the number of agents loaded.
        """
        self._agents.clear()
        found = 0

        for agent_dir in AGENT_DIRS:
            agent_dir = agent_dir.resolve()
            if not agent_dir.is_dir():
                continue

            scope = "project" if ".jarvis" in str(agent_dir) else "user"

            for md_file in sorted(agent_dir.glob("*.md")):
                try:
                    agent = self._load_agent(md_file, scope)
                    if agent is not None:
                        self._agents[agent.name.lower()] = agent
                        found += 1
                except Exception as exc:
                    log.warning("Failed to load agent %s: %s", md_file, exc)

        log.info("Discovered %d custom agent(s)", found)
        return found

    # ── Lookup ───────────────────────────────────────────────────────

    def get(self, name: str) -> Optional[CustomAgent]:
        """Get a custom agent by name (case-insensitive)."""
        return self._agents.get(name.lower())

    def list_agents(self) -> list[CustomAgent]:
        """Return all loaded custom agents."""
        return list(self._agents.values())

    def exists(self, name: str) -> bool:
        """Check if a custom agent exists by name."""
        return name.lower() in self._agents

    def __len__(self) -> int:
        return len(self._agents)

    def __iter__(self):
        return iter(self._agents.values())

    # ── Creation ─────────────────────────────────────────────────────

    def create_agent(
        self,
        name: str,
        description: str,
        system_prompt: str,
        allowed_tools: list[str] | None = None,
        max_iterations: int = 15,
        model: str = "",
        scope: str = "user",
        bash_readonly: bool = False,
    ) -> CustomAgent:
        """Create a new custom agent and save it as a .md file.

        Args:
            name: Agent name (will be kebab-cased for filename)
            description: One-line description
            system_prompt: The system prompt body
            allowed_tools: List of tool names, or None for full access
            max_iterations: Max agent loop iterations
            model: Optional model preference
            scope: "user" (~/.jarvis/agents/) or "project" (.jarvis/agents/)
            bash_readonly: If True, enforce read-only bash

        Returns:
            The created CustomAgent
        """
        # Choose directory
        if scope == "project":
            agent_dir = Path(".jarvis") / "agents"
        else:
            agent_dir = JARVIS_HOME / "agents"

        agent_dir.mkdir(parents=True, exist_ok=True)

        # Build frontmatter
        filename = name.lower().replace(" ", "-").replace("_", "-")
        filepath = agent_dir / f"{filename}.md"

        tools = allowed_tools or TOOL_PRESETS["full"]

        lines = ["---"]
        lines.append(f"name: {name}")
        lines.append(f"description: {description}")
        if model:
            lines.append(f"model: {model}")
        lines.append(f"max_iterations: {max_iterations}")
        if bash_readonly:
            lines.append("bash_readonly: true")
        lines.append("allowed_tools:")
        for tool in tools:
            lines.append(f"  - {tool}")
        lines.append("---")
        lines.append("")
        lines.append(system_prompt)

        filepath.write_text("\n".join(lines))
        log.info("Created custom agent: %s at %s", name, filepath)

        # Load and register
        agent = self._load_agent(filepath, scope)
        if agent:
            self._agents[agent.name.lower()] = agent
        return agent

    # ── Deletion ─────────────────────────────────────────────────────

    def delete_agent(self, name: str) -> bool:
        """Delete a custom agent by name. Returns True if deleted."""
        agent = self.get(name)
        if not agent or not agent.path:
            return False

        try:
            agent.path.unlink()
            del self._agents[name.lower()]
            log.info("Deleted custom agent: %s", name)
            return True
        except Exception as exc:
            log.error("Failed to delete agent %s: %s", name, exc)
            return False

    # ── Generation (LLM-assisted) ────────────────────────────────────

    def build_generation_prompt(self, user_description: str) -> str:
        """Build a prompt for the LLM to generate agent config from user description.

        Returns a prompt string to send to the LLM. The LLM response should
        be parsed with parse_generated_agent().
        """
        existing = ", ".join(a.name for a in self._agents.values()) if self._agents else "none"

        return f"""Generate a JARVIS custom agent based on this description:

"{user_description}"

Existing agents: {existing}

Respond in EXACTLY this format (no other text):
NAME: <short agent name>
DESCRIPTION: <one-line description of when to use this agent>
MODEL: <optional: leave empty or specify a model>
TOOLS: <comma-separated list from: {', '.join(ALL_TOOLS)}>
BASH_READONLY: <true or false>
MAX_ITERATIONS: <number, default 15>
PROMPT:
<the full system prompt for this agent, multiple lines allowed>
END_PROMPT"""

    def parse_generated_agent(self, llm_response: str) -> dict | None:
        """Parse LLM-generated agent config from structured text.

        Returns a dict with keys: name, description, model, tools,
        bash_readonly, max_iterations, prompt. Or None if parsing fails.
        """
        result = {}
        lines = llm_response.strip().splitlines()

        prompt_lines = []
        in_prompt = False

        for line in lines:
            if line.strip() == "END_PROMPT":
                in_prompt = False
                continue

            if in_prompt:
                prompt_lines.append(line)
                continue

            if line.startswith("PROMPT:"):
                in_prompt = True
                # Check if prompt starts on same line
                rest = line[7:].strip()
                if rest:
                    prompt_lines.append(rest)
                continue

            if line.startswith("NAME:"):
                result["name"] = line[5:].strip()
            elif line.startswith("DESCRIPTION:"):
                result["description"] = line[12:].strip()
            elif line.startswith("MODEL:"):
                result["model"] = line[6:].strip()
            elif line.startswith("TOOLS:"):
                tools_str = line[6:].strip()
                result["tools"] = [t.strip() for t in tools_str.split(",") if t.strip()]
            elif line.startswith("BASH_READONLY:"):
                result["bash_readonly"] = line[14:].strip().lower() in ("true", "yes")
            elif line.startswith("MAX_ITERATIONS:"):
                try:
                    result["max_iterations"] = int(line[15:].strip())
                except ValueError:
                    result["max_iterations"] = 15

        if prompt_lines:
            result["prompt"] = "\n".join(prompt_lines).strip()

        # Validate required fields
        if "name" not in result or "prompt" not in result:
            return None

        return result

    # ── Internal ─────────────────────────────────────────────────────

    def _load_agent(self, md_file: Path, scope: str = "user") -> Optional[CustomAgent]:
        """Parse a markdown agent file with YAML frontmatter."""
        content = md_file.read_text(errors="replace")

        fm_match = _FRONTMATTER_RE.match(content)
        if not fm_match:
            log.debug("Skipping %s — no YAML frontmatter", md_file.name)
            return None

        meta = _parse_yaml_lite(fm_match.group(1))
        body = content[fm_match.end():].strip()

        name = meta.get("name", md_file.stem)
        allowed_tools = meta.get("allowed_tools", meta.get("tools", []))
        if isinstance(allowed_tools, str):
            allowed_tools = [allowed_tools]

        max_iters = meta.get("max_iterations", 15)
        if isinstance(max_iters, str):
            try:
                max_iters = int(max_iters)
            except ValueError:
                max_iters = 15

        return CustomAgent(
            name=name,
            description=meta.get("description", ""),
            system_prompt=body,
            allowed_tools=allowed_tools,
            max_iterations=max_iters,
            model=meta.get("model", ""),
            scope=scope,
            path=md_file,
            bash_readonly=meta.get("bash_readonly", False),
        )
