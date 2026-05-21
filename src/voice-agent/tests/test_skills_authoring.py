"""Tests for pipeline/skills_authoring.py — validation + rendering +
guarded writes for user skills."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline import skills_authoring as sa
from pipeline.skills_loader import _parse_skill_file


class TestValidateName:
    def test_ok(self):
        assert sa.validate_name("git-status") is None

    def test_empty(self):
        assert "required" in sa.validate_name("").lower()

    def test_too_long(self):
        assert "exceeds" in sa.validate_name("a" * 65).lower()

    def test_uppercase_rejected(self):
        assert sa.validate_name("GitStatus") is not None

    def test_traversal_rejected(self):
        assert sa.validate_name("../etc") is not None

    def test_leading_hyphen_rejected(self):
        assert sa.validate_name("-foo") is not None


class TestValidateMarkdown:
    def _good(self) -> str:
        return (
            "---\nname: foo\ndescription: a skill\nwhen_to_use: when X\n---\n"
            "# Foo\nDo the thing.\n"
        )

    def test_good(self):
        assert sa.validate_skill_markdown(self._good()) is None

    def test_empty(self):
        assert sa.validate_skill_markdown("") is not None

    def test_no_frontmatter(self):
        assert sa.validate_skill_markdown("# just text") is not None

    def test_missing_name(self):
        assert sa.validate_skill_markdown(
            "---\ndescription: x\n---\nbody"
        ) is not None

    def test_missing_description(self):
        assert sa.validate_skill_markdown("---\nname: x\n---\nbody") is not None

    def test_description_too_long(self):
        desc = "d" * 1025
        content = f"---\nname: x\ndescription: {desc}\n---\nbody"
        assert sa.validate_skill_markdown(content) is not None

    def test_empty_body(self):
        assert sa.validate_skill_markdown(
            "---\nname: x\ndescription: y\n---\n   \n"
        ) is not None

    def test_oversize(self):
        big = "x" * (sa.MAX_SKILL_CONTENT_CHARS + 1)
        content = f"---\nname: x\ndescription: y\n---\n{big}"
        assert sa.validate_skill_markdown(content) is not None


class TestRender:
    def test_round_trips(self, tmp_path):
        content = sa.render_skill_md(
            "my-skill", "does a thing", "when the user wants a thing",
            "# My Skill\nStep one.\n",
        )
        # Renders valid markdown
        assert sa.validate_skill_markdown(content) is None
        # And parses back to the same fields
        f = tmp_path / "SKILL.md"
        f.write_text(content)
        sk = _parse_skill_file(f)
        assert sk.name == "my-skill"
        assert sk.description == "does a thing"
        assert sk.when_to_use == "when the user wants a thing"
        assert "Step one." in sk.body

    def test_multiline_when_to_use_round_trips(self, tmp_path):
        content = sa.render_skill_md(
            "ml", "desc", "Line one.\nLine two.", "# Body\ntext",
        )
        assert sa.validate_skill_markdown(content) is None
        f = tmp_path / "SKILL.md"
        f.write_text(content)
        sk = _parse_skill_file(f)
        assert sk.when_to_use == "Line one.\nLine two."


class TestCreate:
    def test_creates_and_reloads(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JARVIS_SKILLS_PATHS", str(tmp_path))
        res = sa.create_user_skill(
            "spotify-control", "control playback", "when user wants music",
            "# Spotify\nUse dbus.\n",
        )
        assert res["ok"] is True
        written = tmp_path / "spotify-control" / "SKILL.md"
        assert written.exists()
        assert "control playback" in written.read_text()
        # reload_skills() ran → registry sees it immediately
        from pipeline.skills_loader import SKILLS
        assert SKILLS.get("spotify-control") is not None

    def test_bad_name_rejected_no_write(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JARVIS_SKILLS_PATHS", str(tmp_path))
        res = sa.create_user_skill("Bad Name", "d", "w", "# body\ntext")
        assert res["ok"] is False
        assert not (tmp_path / "Bad Name").exists()

    def test_empty_description_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JARVIS_SKILLS_PATHS", str(tmp_path))
        res = sa.create_user_skill("ok-name", "", "w", "# body\ntext")
        assert res["ok"] is False

    def test_atomic_no_tmp_left_behind(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JARVIS_SKILLS_PATHS", str(tmp_path))
        sa.create_user_skill("clean", "d", "w", "# b\ntext")
        leftovers = list((tmp_path / "clean").glob(".tmp-*"))
        assert leftovers == []

    def test_shadow_flag_when_shipped_exists(self, tmp_path, monkeypatch):
        shipped = tmp_path / "shipped"
        user = tmp_path / "user"
        (shipped / "dup").mkdir(parents=True)
        (shipped / "dup" / "SKILL.md").write_text(
            "---\nname: dup\ndescription: shipped\n---\nbody"
        )
        monkeypatch.setenv("JARVIS_SKILLS_PATHS", f"{shipped}:{user}")
        res = sa.create_user_skill("dup", "user version", "w", "# b\ntext")
        assert res["ok"] is True
        assert res["shadow"] is True
