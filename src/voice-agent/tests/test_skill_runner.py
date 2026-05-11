"""Tests for tools/skill_runner.py — list_skills + run_skill tools.

Covers:
  - list_skills returns voice-friendly inventory shape
  - run_skill returns the body with a header on success
  - run_skill returns an error string (not exception) on unknown name
  - Empty registry case for both tools
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline.skills_loader import Skill, SkillsRegistry, load_skills


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _impl(tool):
    """Unwrap @function_tool to call the inner coroutine."""
    return getattr(tool, "_func", tool)


def _seed_registry(tmp_path: Path, skills: dict[str, str]) -> SkillsRegistry:
    """Helper — build a tmp directory tree with skill files and load it."""
    for name, desc in skills.items():
        d = tmp_path / name
        d.mkdir(exist_ok=True)
        (d / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {desc}\nwhen_to_use: {desc}\n---\n"
            f"body for {name}: do the thing."
        )
    return load_skills([tmp_path])


class TestListSkills:
    def test_empty_registry(self, tmp_path):
        load_skills([tmp_path])  # empty dir
        from tools.skill_runner import list_skills
        out = run(_impl(list_skills)())
        assert "no skills" in out.lower()

    def test_lists_all_with_when_to_use(self, tmp_path):
        _seed_registry(tmp_path, {
            "spotify-control": "play / pause / skip Spotify",
            "git-status": "summarize repo state",
        })
        from tools.skill_runner import list_skills
        out = run(_impl(list_skills)())
        assert "spotify-control" in out
        assert "git-status" in out
        assert "play / pause" in out
        assert "summarize repo state" in out

    def test_count_reflects_registry_size(self, tmp_path):
        _seed_registry(tmp_path, {"a": "first", "b": "second", "c": "third"})
        from tools.skill_runner import list_skills
        out = run(_impl(list_skills)())
        assert "3 skill" in out


class TestRunSkill:
    def test_returns_body_on_success(self, tmp_path):
        _seed_registry(tmp_path, {"foo": "the foo skill"})
        from tools.skill_runner import run_skill
        out = run(_impl(run_skill)(name="foo"))
        assert "SKILL: foo" in out
        assert "body for foo" in out

    def test_unknown_skill_returns_error_string(self, tmp_path):
        _seed_registry(tmp_path, {"foo": "the foo skill"})
        from tools.skill_runner import run_skill
        out = run(_impl(run_skill)(name="bogus"))
        assert "unknown skill" in out.lower()
        assert "foo" in out  # lists available names

    def test_unknown_skill_with_empty_registry(self, tmp_path):
        load_skills([tmp_path])
        from tools.skill_runner import run_skill
        out = run(_impl(run_skill)(name="anything"))
        assert "unknown skill" in out.lower()
        assert "(none)" in out

    def test_strips_whitespace_from_name(self, tmp_path):
        _seed_registry(tmp_path, {"foo": "the foo skill"})
        from tools.skill_runner import run_skill
        out = run(_impl(run_skill)(name="  foo  "))
        assert "SKILL: foo" in out


class TestToolShape:
    def test_tools_are_function_tools(self):
        from tools.skill_runner import list_skills, run_skill
        assert hasattr(list_skills, "info")
        assert list_skills.info.name == "list_skills"
        assert hasattr(run_skill, "info")
        assert run_skill.info.name == "run_skill"
