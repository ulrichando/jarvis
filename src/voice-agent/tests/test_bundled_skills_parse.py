"""Smoke-test: every skills/*/SKILL.md bundled into src/voice-agent/skills/
parses via _parse_skill_file and passes validate_skill_markdown.

This catches malformed frontmatter, missing required fields, or
over-size files introduced during the bulk-integrate pass (or later
per-skill edits) before they silently degrade the runtime registry.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline.skills_authoring import validate_skill_markdown
from pipeline.skills_loader import _parse_skill_file

# The shipped bundled skills live one level above tests/.
_SKILLS_ROOT = Path(__file__).parent.parent / "skills"


def _bundled_skill_files() -> list[Path]:
    """Return all skills/*/SKILL.md paths (non-recursive — loader is one-level)."""
    paths = []
    for sub in _SKILLS_ROOT.iterdir():
        if sub.is_dir():
            f = sub / "SKILL.md"
            if f.exists():
                paths.append(f)
    return sorted(paths)


@pytest.mark.parametrize("skill_path", _bundled_skill_files(), ids=lambda p: p.parent.name)
def test_bundled_skill_parses(skill_path: Path) -> None:
    """_parse_skill_file must return a Skill (not None) for every bundled skill."""
    sk = _parse_skill_file(skill_path)
    assert sk is not None, (
        f"{skill_path} failed to parse — check frontmatter has 'name' and 'description'."
    )
    assert sk.name, f"{skill_path}: skill.name is empty after parse"
    assert sk.description, f"{skill_path}: skill.description is empty after parse"


@pytest.mark.parametrize("skill_path", _bundled_skill_files(), ids=lambda p: p.parent.name)
def test_bundled_skill_validates(skill_path: Path) -> None:
    """validate_skill_markdown must return None (no error) for every bundled skill."""
    content = skill_path.read_text(encoding="utf-8")
    error = validate_skill_markdown(content)
    assert error is None, (
        f"{skill_path} failed validation: {error}"
    )
