"""JARVIS Skills — markdown-based skill templates with YAML frontmatter."""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from src.config import JARVIS_HOME

logger = logging.getLogger(__name__)

# Skill directories: global and local
SKILL_DIRS = [
    JARVIS_HOME / "skills",
    Path(".jarvis") / "skills",
]

# Frontmatter parser — avoids PyYAML dependency
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_yaml_lite(text: str) -> dict:
    """Minimal YAML parser for skill frontmatter.

    Handles:
      key: value
      key: [a, b, c]
      key:
        - item1
        - item2
      key: true/false
    """
    result: dict = {}
    lines = text.splitlines()
    current_key: Optional[str] = None
    current_list: Optional[list] = None

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # List continuation
        if stripped.startswith("- ") and current_key is not None:
            if current_list is None:
                current_list = []
                result[current_key] = current_list
            current_list.append(stripped[2:].strip().strip('"').strip("'"))
            continue

        # Key: value
        if ":" in stripped:
            current_list = None
            key, _, value = stripped.partition(":")
            key = key.strip()
            value = value.strip()
            current_key = key

            if not value:
                # Value will come as list items below
                result[key] = []
                current_list = result[key]
            elif value.startswith("[") and value.endswith("]"):
                # Inline list
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
class Skill:
    """A loaded skill definition."""
    name: str
    description: str = ""
    prompt_template: str = ""
    triggers: list[str] = field(default_factory=list)
    user_invocable: bool = True
    model_invocable: bool = False
    hooks: dict = field(default_factory=dict)
    allowed_tools: list[str] = field(default_factory=list)
    path: Optional[Path] = None

    def render(self, args: str = "") -> str:
        """Render the prompt template, replacing {{args}} and {{input}}."""
        text = self.prompt_template
        text = text.replace("{{args}}", args)
        text = text.replace("{{input}}", args)
        text = text.replace("{{ args }}", args)
        text = text.replace("{{ input }}", args)
        return text


class SkillManager:
    """Discover and manage markdown-based skill files."""

    def __init__(self):
        self._skills: dict[str, Skill] = {}

    # ── Discovery ─────────────────────────────────────────────────────

    def discover(self) -> int:
        """Scan skill directories and load all .md skill files.

        Returns the number of skills loaded.
        """
        self._skills.clear()
        found = 0

        for skill_dir in SKILL_DIRS:
            skill_dir = skill_dir.resolve()
            if not skill_dir.is_dir():
                continue
            for md_file in sorted(skill_dir.glob("*.md")):
                try:
                    skill = self._load_skill(md_file)
                    if skill is not None:
                        self._skills[skill.name] = skill
                        found += 1
                except Exception as exc:
                    logger.warning("Failed to load skill %s: %s", md_file, exc)

        logger.info("Discovered %d skill(s)", found)
        return found

    # ── Lookup ────────────────────────────────────────────────────────

    def get(self, name: str) -> Optional[Skill]:
        """Get a skill by name (case-insensitive)."""
        return self._skills.get(name) or self._skills.get(name.lower())

    def match_for_query(self, query: str) -> Optional[Skill]:
        """Find the best-matching skill for a query based on triggers.

        Returns the skill with the most trigger matches, or None.
        """
        q_lower = query.lower()
        best: Optional[Skill] = None
        best_score = 0

        for skill in self._skills.values():
            if not skill.triggers:
                continue
            score = sum(1 for t in skill.triggers if t.lower() in q_lower)
            if score > best_score:
                best_score = score
                best = skill

        return best

    def list_skills(self) -> list[dict]:
        """Return metadata for all loaded skills."""
        return [
            {
                "name": s.name,
                "description": s.description,
                "triggers": s.triggers,
                "user_invocable": s.user_invocable,
                "model_invocable": s.model_invocable,
            }
            for s in self._skills.values()
        ]

    def __len__(self) -> int:
        return len(self._skills)

    def __iter__(self):
        return iter(self._skills.values())

    # ── Internal ──────────────────────────────────────────────────────

    def _load_skill(self, md_file: Path) -> Optional[Skill]:
        """Parse a markdown skill file with YAML frontmatter."""
        content = md_file.read_text(errors="replace")

        # Extract frontmatter
        fm_match = _FRONTMATTER_RE.match(content)
        if not fm_match:
            logger.debug("Skipping %s — no YAML frontmatter", md_file.name)
            return None

        meta = _parse_yaml_lite(fm_match.group(1))
        body = content[fm_match.end():].strip()

        name = meta.get("name", md_file.stem)
        triggers = meta.get("triggers", [])
        if isinstance(triggers, str):
            triggers = [triggers]

        allowed_tools = meta.get("allowed_tools", meta.get("tools", []))
        if isinstance(allowed_tools, str):
            allowed_tools = [allowed_tools]

        hooks = meta.get("hooks", {})
        if not isinstance(hooks, dict):
            hooks = {}

        return Skill(
            name=name,
            description=meta.get("description", ""),
            prompt_template=body,
            triggers=[t.lower() for t in triggers],
            user_invocable=meta.get("user_invocable", True),
            model_invocable=meta.get("model_invocable", False),
            hooks=hooks,
            allowed_tools=allowed_tools,
            path=md_file,
        )
