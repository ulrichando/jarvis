"""Load skills from a directory of skill definition files."""

from __future__ import annotations

import os
from typing import Any, Optional


def load_skills_dir(dir_path: str) -> dict[str, Any]:
    """Load all skills from a directory."""
    skills: dict[str, Any] = {}
    if not os.path.isdir(dir_path):
        return skills
    for filename in os.listdir(dir_path):
        if not filename.endswith((".md", ".yaml", ".yml")):
            continue
        skill_name = os.path.splitext(filename)[0]
        filepath = os.path.join(dir_path, filename)
        try:
            with open(filepath) as f:
                content = f.read()
            skills[skill_name] = {
                "name": skill_name,
                "path": filepath,
                "content": content,
            }
        except Exception:
            pass
    return skills
