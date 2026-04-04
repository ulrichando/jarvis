"""Skill management utilities for JARVIS.

Handles discovery, listing, searching, and validation of .md skill files
with YAML frontmatter.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml


SkillSource = Literal["built-in", "user", "project"]


@dataclass
class SkillInfo:
    """Metadata for a single skill file."""

    name: str
    description: str
    source: SkillSource
    user_invocable: bool = True
    model_invocable: bool = False
    path: str = ""


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Extract YAML frontmatter and body from a markdown file.

    Returns (metadata_dict, body_text). If no frontmatter is found,
    metadata_dict is empty.
    """
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)", text, re.DOTALL)
    if not match:
        return {}, text
    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        meta = {}
    return meta, match.group(2)


def _source_for_dir(dir_path: str) -> SkillSource:
    """Determine the source label for a skill directory."""
    home = os.path.expanduser("~")
    jarvis_home = os.environ.get("JARVIS_HOME", os.path.join(home, ".jarvis"))

    if dir_path.startswith(jarvis_home):
        return "user"
    # Check common built-in locations
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if dir_path.startswith(project_root):
        return "built-in"
    return "project"


def list_skills(skill_dirs: list[str]) -> list[SkillInfo]:
    """Scan directories for .md skill files and parse their frontmatter.

    Args:
        skill_dirs: List of directory paths to scan for skill files.

    Returns:
        List of SkillInfo objects found across all directories.
    """
    skills: list[SkillInfo] = []

    for dir_path in skill_dirs:
        if not os.path.isdir(dir_path):
            continue

        source = _source_for_dir(dir_path)

        for entry in sorted(os.listdir(dir_path)):
            if not entry.endswith(".md"):
                continue

            filepath = os.path.join(dir_path, entry)
            if not os.path.isfile(filepath):
                continue

            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()
            except OSError:
                continue

            meta, _body = _parse_frontmatter(content)
            name = meta.get("name", Path(entry).stem)
            description = meta.get("description", "")
            user_invocable = bool(meta.get("user_invocable", True))
            model_invocable = bool(meta.get("model_invocable", False))

            skills.append(
                SkillInfo(
                    name=name,
                    description=description,
                    source=source,
                    user_invocable=user_invocable,
                    model_invocable=model_invocable,
                    path=filepath,
                )
            )

    return skills


def format_skill_list(skills: list[SkillInfo]) -> str:
    """Format skills for CLI display, grouped by source.

    Args:
        skills: List of SkillInfo objects.

    Returns:
        Formatted multi-line string suitable for terminal output.
    """
    if not skills:
        return "No skills found. Create skills in .jarvis/skills/ or ~/.jarvis/skills/"

    source_order: list[SkillSource] = ["built-in", "user", "project"]
    source_labels = {
        "built-in": "Built-in skills",
        "user": "User skills",
        "project": "Project skills",
    }

    grouped: dict[SkillSource, list[SkillInfo]] = {s: [] for s in source_order}
    for skill in skills:
        grouped[skill.source].append(skill)

    lines: list[str] = []
    total = len(skills)
    lines.append(f"{total} skill{'s' if total != 1 else ''}")
    lines.append("")

    for source in source_order:
        group = grouped[source]
        if not group:
            continue

        lines.append(f"  {source_labels[source]}")
        for skill in group:
            invoke_tags: list[str] = []
            if skill.user_invocable:
                invoke_tags.append("user")
            if skill.model_invocable:
                invoke_tags.append("model")
            tags = f" [{', '.join(invoke_tags)}]" if invoke_tags else ""
            desc = f" - {skill.description}" if skill.description else ""
            lines.append(f"    /{skill.name}{desc}{tags}")
        lines.append("")

    return "\n".join(lines).rstrip()


def search_skills(skills: list[SkillInfo], query: str) -> list[SkillInfo]:
    """Fuzzy search skills by name and description.

    Matches if every word in the query appears (case-insensitive) in
    the skill's name or description.

    Args:
        skills: List of SkillInfo objects to search.
        query: Search query string.

    Returns:
        Filtered list of matching SkillInfo objects, ordered by
        relevance (name match first).
    """
    if not query.strip():
        return list(skills)

    terms = query.lower().split()
    results: list[tuple[int, SkillInfo]] = []

    for skill in skills:
        name_lower = skill.name.lower()
        desc_lower = skill.description.lower()
        haystack = f"{name_lower} {desc_lower}"

        if all(term in haystack for term in terms):
            # Score: name matches are ranked higher
            name_hits = sum(1 for t in terms if t in name_lower)
            score = -name_hits  # negative for ascending sort
            results.append((score, skill))

    results.sort(key=lambda x: x[0])
    return [s for _, s in results]


def validate_skill(
    name: str, description: str, prompt_template: str
) -> dict[str, list[str]]:
    """Validate a skill definition and return errors/warnings.

    Args:
        name: Skill name.
        description: Skill description.
        prompt_template: The prompt template body.

    Returns:
        Dict with 'errors' and 'warnings' lists of strings.
    """
    errors: list[str] = []
    warnings: list[str] = []

    # Name validation
    if not name:
        errors.append("Skill name is required")
    elif not re.match(r"^[a-zA-Z][a-zA-Z0-9_-]*$", name):
        errors.append(
            "Skill name must start with a letter and contain only "
            "letters, digits, hyphens, and underscores"
        )
    elif len(name) > 64:
        errors.append("Skill name must be 64 characters or fewer")

    # Description validation
    if not description:
        warnings.append("Skill should have a description for discoverability")
    elif len(description) > 256:
        warnings.append("Skill description is very long; consider shortening")

    # Template validation
    if not prompt_template.strip():
        errors.append("Prompt template is empty")
    else:
        # Check for common template variables
        if "{{" not in prompt_template:
            warnings.append(
                "Prompt template has no {{args}} placeholder; "
                "user input will not be interpolated"
            )

    return {"errors": errors, "warnings": warnings}
