"""Tests for tools/skills_tool.py — skills_list / skill_view / skill_manage.

Proves:
  1. All three tools register in the ToolRegistry and appear in
     load_all_livekit_tools().
  2. skills_list returns a voice-friendly summary of discovered skills.
  3. skill_view returns a named skill's full markdown body; returns a
     "(unknown skill: ...)" hint for unknown names.
  4. skill_manage create → creates a user skill, list sees it, view shows it.
  5. skill_manage patch → targeted replacement in an existing user skill.
  6. skill_manage edit → full body rewrite, frontmatter preserved.
  7. skill_manage delete → skill moved to trash, registry updated.
  8. Validation failures (bad name, empty body, bad action) return error strings
     — never raise.

Isolation: every test that writes uses JARVIS_SKILLS_PATHS pointing at a
tmp_path, exactly like the existing skills_authoring tests, so no files escape
to ~/.jarvis/skills/ during the test run.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

# Make the voice-agent package root importable regardless of pytest rootdir.
_VOICE_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_VOICE_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_VOICE_AGENT_ROOT))


def _run(coro):
    """Run a coroutine on a throwaway event loop — mirrors test_tool_adapter.py."""
    return asyncio.new_event_loop().run_until_complete(coro)


def _invoke(tool, args: dict) -> str:
    """Invoke a RawFunctionTool as the framework does — always returns awaited str."""
    return _run(tool(raw_arguments=args))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_skill(root: Path, name: str, description: str, body: str = "# Body\nDo the thing.\n") -> None:
    """Write a minimal valid SKILL.md under root/<name>/SKILL.md."""
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\nwhen_to_use: {description}\n---\n{body}"
    )


# ---------------------------------------------------------------------------
# 1. Registration
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_tools_registered_in_registry(self):
        from tools.registry import registry

        # Importing skills_tool triggers the registry.register() side effects.
        import tools.skills_tool  # noqa: F401

        assert registry.get_entry("skills_list") is not None
        assert registry.get_entry("skill_view") is not None
        assert registry.get_entry("skill_manage") is not None

    def test_all_three_in_load_all_livekit_tools(self):
        from tools._adapter import load_all_livekit_tools

        tools = load_all_livekit_tools()
        names = {t.info.name for t in tools}
        assert "skills_list" in names
        assert "skill_view" in names
        assert "skill_manage" in names

    def test_tools_are_raw_function_tools(self):
        from livekit.agents.llm import is_raw_function_tool
        from tools._adapter import load_all_livekit_tools

        tools = load_all_livekit_tools()
        skill_tools = [t for t in tools if t.info.name in {"skills_list", "skill_view", "skill_manage"}]
        assert len(skill_tools) == 3
        assert all(is_raw_function_tool(t) for t in skill_tools)


# ---------------------------------------------------------------------------
# 2. skills_list
# ---------------------------------------------------------------------------


class TestSkillsList:
    def test_empty_registry_returns_informative_message(self, monkeypatch):
        monkeypatch.setenv("JARVIS_SKILLS_PATHS", "/tmp/__nonexistent_jarvis_skills_test__")
        from pipeline.skills_loader import load_skills
        load_skills()  # reload against empty dir

        from tools._adapter import load_all_livekit_tools
        tools = load_all_livekit_tools()
        skills_list = next(t for t in tools if t.info.name == "skills_list")
        result = _invoke(skills_list, {})
        assert "No skills" in result or "0" in result or "skill" in result.lower()

    def test_lists_discovered_skills(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JARVIS_SKILLS_PATHS", str(tmp_path))
        _make_skill(tmp_path, "git-status", "show git status")
        _make_skill(tmp_path, "weather-check", "check the weather")
        from pipeline.skills_loader import load_skills
        load_skills()

        from tools._adapter import load_all_livekit_tools
        tools = load_all_livekit_tools()
        skills_list = next(t for t in tools if t.info.name == "skills_list")
        result = _invoke(skills_list, {})

        assert "git-status" in result
        assert "weather-check" in result
        assert "2 skill" in result  # "2 skill(s) available:"

    def test_includes_when_to_use_in_output(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JARVIS_SKILLS_PATHS", str(tmp_path))
        _make_skill(tmp_path, "my-skill", "does something useful")
        from pipeline.skills_loader import load_skills
        load_skills()

        from tools._adapter import load_all_livekit_tools
        tools = load_all_livekit_tools()
        skills_list = next(t for t in tools if t.info.name == "skills_list")
        result = _invoke(skills_list, {})
        assert "does something useful" in result


# ---------------------------------------------------------------------------
# 3. skill_view
# ---------------------------------------------------------------------------


class TestSkillView:
    def test_returns_body_for_known_skill(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JARVIS_SKILLS_PATHS", str(tmp_path))
        _make_skill(tmp_path, "demo", "a demo skill", "# Demo\nStep one.\nStep two.\n")
        from pipeline.skills_loader import load_skills
        load_skills()

        from tools._adapter import load_all_livekit_tools
        tools = load_all_livekit_tools()
        skill_view = next(t for t in tools if t.info.name == "skill_view")

        result = _invoke(skill_view, {"name": "demo"})
        assert "demo" in result
        assert "Step one." in result
        assert "Step two." in result

    def test_includes_header_fields(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JARVIS_SKILLS_PATHS", str(tmp_path))
        _make_skill(tmp_path, "demo2", "demo skill two", "# D2\nBody.\n")
        from pipeline.skills_loader import load_skills
        load_skills()

        from tools._adapter import load_all_livekit_tools
        tools = load_all_livekit_tools()
        skill_view = next(t for t in tools if t.info.name == "skill_view")

        result = _invoke(skill_view, {"name": "demo2"})
        assert "demo skill two" in result  # description in header

    def test_unknown_skill_returns_hint(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JARVIS_SKILLS_PATHS", str(tmp_path))
        _make_skill(tmp_path, "real-skill", "the real one")
        from pipeline.skills_loader import load_skills
        load_skills()

        from tools._adapter import load_all_livekit_tools
        tools = load_all_livekit_tools()
        skill_view = next(t for t in tools if t.info.name == "skill_view")

        result = _invoke(skill_view, {"name": "ghost-skill"})
        assert "unknown skill" in result.lower() or "ghost-skill" in result
        # Should hint at available names
        assert "real-skill" in result

    def test_missing_name_param_returns_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JARVIS_SKILLS_PATHS", str(tmp_path))
        from pipeline.skills_loader import load_skills
        load_skills()

        from tools._adapter import load_all_livekit_tools
        tools = load_all_livekit_tools()
        skill_view = next(t for t in tools if t.info.name == "skill_view")

        result = _invoke(skill_view, {})
        assert "required" in result.lower() or "name" in result.lower()


# ---------------------------------------------------------------------------
# 4–7. skill_manage
# ---------------------------------------------------------------------------


class TestSkillManageCreate:
    def test_create_persists_and_appears_in_list(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JARVIS_SKILLS_PATHS", str(tmp_path))
        from pipeline.skills_loader import load_skills
        load_skills()

        from tools._adapter import load_all_livekit_tools
        tools = load_all_livekit_tools()
        skill_manage = next(t for t in tools if t.info.name == "skill_manage")
        skills_list = next(t for t in tools if t.info.name == "skills_list")
        skill_view = next(t for t in tools if t.info.name == "skill_view")

        # Create
        result = _invoke(skill_manage, {
            "action": "create",
            "name": "spotify-ctrl",
            "description": "control Spotify playback",
            "when_to_use": "user wants music",
            "body": "# Spotify\nUse dbus to control playback.\n",
        })
        assert "created" in result.lower() or "ok" in result.lower()
        # Skill file should exist
        assert (tmp_path / "spotify-ctrl" / "SKILL.md").exists()

        # List should include it
        list_result = _invoke(skills_list, {})
        assert "spotify-ctrl" in list_result

        # View should return the body
        view_result = _invoke(skill_view, {"name": "spotify-ctrl"})
        assert "dbus" in view_result

    def test_create_bad_name_returns_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JARVIS_SKILLS_PATHS", str(tmp_path))
        from pipeline.skills_loader import load_skills
        load_skills()

        from tools._adapter import load_all_livekit_tools
        tools = load_all_livekit_tools()
        skill_manage = next(t for t in tools if t.info.name == "skill_manage")

        result = _invoke(skill_manage, {
            "action": "create",
            "name": "Bad Name",  # uppercase + space — invalid
            "description": "d",
            "body": "# B\ntext",
        })
        assert "error" in result.lower() or "invalid" in result.lower()
        assert not (tmp_path / "Bad Name").exists()

    def test_create_missing_description_returns_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JARVIS_SKILLS_PATHS", str(tmp_path))
        from pipeline.skills_loader import load_skills
        load_skills()

        from tools._adapter import load_all_livekit_tools
        tools = load_all_livekit_tools()
        skill_manage = next(t for t in tools if t.info.name == "skill_manage")

        result = _invoke(skill_manage, {
            "action": "create",
            "name": "ok-name",
            "description": "",  # empty
            "body": "# B\ntext",
        })
        assert "description" in result.lower() or "error" in result.lower()

    def test_create_missing_body_returns_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JARVIS_SKILLS_PATHS", str(tmp_path))
        from pipeline.skills_loader import load_skills
        load_skills()

        from tools._adapter import load_all_livekit_tools
        tools = load_all_livekit_tools()
        skill_manage = next(t for t in tools if t.info.name == "skill_manage")

        result = _invoke(skill_manage, {
            "action": "create",
            "name": "ok-name",
            "description": "desc",
            "body": "",  # missing
        })
        assert "body" in result.lower() or "error" in result.lower()


class TestSkillManagePatch:
    def _seed(self, tmp_path, monkeypatch, name: str = "notes") -> None:
        monkeypatch.setenv("JARVIS_SKILLS_PATHS", str(tmp_path))
        from pipeline import skills_authoring as sa
        sa.create_user_skill(name, "take notes", "when noting", "# Notes\nv1 body.\n")

    def test_patch_replaces_text(self, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch)
        from tools._adapter import load_all_livekit_tools
        tools = load_all_livekit_tools()
        skill_manage = next(t for t in tools if t.info.name == "skill_manage")

        result = _invoke(skill_manage, {
            "action": "patch",
            "name": "notes",
            "old_string": "v1 body.",
            "new_string": "v2 body.",
        })
        assert "patched" in result.lower() or "ok" in result.lower()
        assert "v2 body." in (tmp_path / "notes" / "SKILL.md").read_text()

    def test_patch_missing_old_string_returns_error(self, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch)
        from tools._adapter import load_all_livekit_tools
        tools = load_all_livekit_tools()
        skill_manage = next(t for t in tools if t.info.name == "skill_manage")

        result = _invoke(skill_manage, {"action": "patch", "name": "notes"})
        assert "required" in result.lower() or "error" in result.lower()


class TestSkillManageEdit:
    def test_edit_rewrites_body_keeps_meta(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JARVIS_SKILLS_PATHS", str(tmp_path))
        from pipeline import skills_authoring as sa
        sa.create_user_skill("journal", "keep a journal", "when journaling", "# J\nold body.\n")

        from tools._adapter import load_all_livekit_tools
        tools = load_all_livekit_tools()
        skill_manage = next(t for t in tools if t.info.name == "skill_manage")

        result = _invoke(skill_manage, {
            "action": "edit",
            "name": "journal",
            "body": "# Journal\nnew body content.\n",
        })
        assert "updated" in result.lower() or "ok" in result.lower()
        text = (tmp_path / "journal" / "SKILL.md").read_text()
        assert "new body content." in text
        assert "keep a journal" in text  # description preserved

    def test_edit_missing_body_returns_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JARVIS_SKILLS_PATHS", str(tmp_path))
        from pipeline import skills_authoring as sa
        sa.create_user_skill("journal2", "journal", "when journaling", "# J\nbody.\n")

        from tools._adapter import load_all_livekit_tools
        tools = load_all_livekit_tools()
        skill_manage = next(t for t in tools if t.info.name == "skill_manage")

        result = _invoke(skill_manage, {"action": "edit", "name": "journal2", "body": ""})
        assert "body" in result.lower() or "error" in result.lower()


class TestSkillManageDelete:
    def test_delete_moves_to_trash(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JARVIS_SKILLS_PATHS", str(tmp_path))
        from pipeline import skills_authoring as sa
        sa.create_user_skill("temp-skill", "temporary", "when temp", "# T\nbody.\n")

        from tools._adapter import load_all_livekit_tools
        tools = load_all_livekit_tools()
        skill_manage = next(t for t in tools if t.info.name == "skill_manage")

        result = _invoke(skill_manage, {"action": "delete", "name": "temp-skill"})
        assert "deleted" in result.lower() or "trash" in result.lower()
        assert not (tmp_path / "temp-skill" / "SKILL.md").exists()

        # Registry no longer sees it
        from pipeline.skills_loader import SKILLS
        assert SKILLS.get("temp-skill") is None

    def test_delete_unknown_returns_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JARVIS_SKILLS_PATHS", str(tmp_path))
        from pipeline.skills_loader import load_skills
        load_skills()

        from tools._adapter import load_all_livekit_tools
        tools = load_all_livekit_tools()
        skill_manage = next(t for t in tools if t.info.name == "skill_manage")

        result = _invoke(skill_manage, {"action": "delete", "name": "ghost"})
        assert "error" in result.lower() or "no skill" in result.lower()


# ---------------------------------------------------------------------------
# 8. Validation edge cases
# ---------------------------------------------------------------------------


class TestValidationEdgeCases:
    def test_unknown_action_returns_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JARVIS_SKILLS_PATHS", str(tmp_path))
        from pipeline.skills_loader import load_skills
        load_skills()

        from tools._adapter import load_all_livekit_tools
        tools = load_all_livekit_tools()
        skill_manage = next(t for t in tools if t.info.name == "skill_manage")

        result = _invoke(skill_manage, {"action": "obliterate", "name": "x"})
        assert "unknown action" in result.lower() or "error" in result.lower()

    def test_missing_name_returns_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JARVIS_SKILLS_PATHS", str(tmp_path))
        from pipeline.skills_loader import load_skills
        load_skills()

        from tools._adapter import load_all_livekit_tools
        tools = load_all_livekit_tools()
        skill_manage = next(t for t in tools if t.info.name == "skill_manage")

        result = _invoke(skill_manage, {"action": "create"})  # no name
        assert "name" in result.lower() or "required" in result.lower()
