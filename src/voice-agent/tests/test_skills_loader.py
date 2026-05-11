"""Tests for pipeline/skills_loader.py — discovery + parsing of
Claude-Code-style SKILL.md files.

Covers:
  - Frontmatter parsing (single-line, block-scalar |, quoted values)
  - Skill file parsing (valid file, missing frontmatter, missing name,
    missing description)
  - Directory discovery (Claude-Code <name>/SKILL.md style + flat <name>.md)
  - User-root overrides shipped-root on name collision
  - JARVIS_SKILLS_PATHS env override
  - The bundled skills (git-status, system-stats) parse successfully
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline.skills_loader import (
    Skill,
    SkillsRegistry,
    _parse_frontmatter,
    _parse_skill_file,
    discover_skills,
    load_skills,
)


# ── Frontmatter parser ─────────────────────────────────────────────


class TestFrontmatterParser:
    def test_simple_kv(self):
        text = "---\nname: foo\ndescription: a skill\n---\nbody"
        fm, body = _parse_frontmatter(text)
        assert fm == {"name": "foo", "description": "a skill"}
        assert body == "body"

    def test_quoted_value(self):
        text = '---\nname: "spotify-control"\ndescription: \'play music\'\n---\nbody'
        fm, body = _parse_frontmatter(text)
        assert fm["name"] == "spotify-control"
        assert fm["description"] == "play music"

    def test_block_scalar(self):
        text = (
            "---\n"
            "name: x\n"
            "when_to_use: |\n"
            "  User wants something.\n"
            "  Multi-line description.\n"
            "---\n"
            "body"
        )
        fm, body = _parse_frontmatter(text)
        assert fm["name"] == "x"
        assert "User wants something" in fm["when_to_use"]
        assert "Multi-line description" in fm["when_to_use"]

    def test_no_frontmatter(self):
        text = "Just plain markdown with no frontmatter."
        fm, body = _parse_frontmatter(text)
        assert fm == {}
        assert body == text

    def test_block_scalar_preserves_newlines(self):
        text = (
            "---\n"
            "name: x\n"
            "when_to_use: |\n"
            "  Line one.\n"
            "  Line two.\n"
            "---\n"
        )
        fm, _ = _parse_frontmatter(text)
        assert fm["when_to_use"] == "Line one.\nLine two."


# ── Skill file parser ──────────────────────────────────────────────


class TestSkillFileParser:
    def test_valid_file(self, tmp_path):
        f = tmp_path / "SKILL.md"
        f.write_text(
            "---\n"
            "name: test-skill\n"
            "description: A test skill\n"
            "when_to_use: For testing\n"
            "---\n"
            "# Recipe\n"
            "Do the thing.\n"
        )
        sk = _parse_skill_file(f)
        assert sk is not None
        assert sk.name == "test-skill"
        assert sk.description == "A test skill"
        assert sk.when_to_use == "For testing"
        assert "Do the thing" in sk.body

    def test_missing_frontmatter_skipped(self, tmp_path):
        f = tmp_path / "SKILL.md"
        f.write_text("# Just a header\nNo frontmatter.")
        assert _parse_skill_file(f) is None

    def test_missing_name_skipped(self, tmp_path):
        f = tmp_path / "SKILL.md"
        f.write_text("---\ndescription: no name\n---\nbody")
        assert _parse_skill_file(f) is None

    def test_missing_description_skipped(self, tmp_path):
        f = tmp_path / "SKILL.md"
        f.write_text("---\nname: x\n---\nbody")
        assert _parse_skill_file(f) is None

    def test_when_to_use_defaults_to_description(self, tmp_path):
        f = tmp_path / "SKILL.md"
        f.write_text(
            "---\nname: x\ndescription: do X\n---\nbody"
        )
        sk = _parse_skill_file(f)
        assert sk.when_to_use == "do X"


# ── Directory discovery ────────────────────────────────────────────


class TestDiscovery:
    def _make_skill_dir(self, root: Path, name: str, desc: str) -> Path:
        d = root / name
        d.mkdir(parents=True, exist_ok=True)
        f = d / "SKILL.md"
        f.write_text(
            f"---\nname: {name}\ndescription: {desc}\n---\nbody for {name}"
        )
        return f

    def test_discovers_skill_md_in_subdirs(self, tmp_path):
        self._make_skill_dir(tmp_path, "foo", "the foo skill")
        self._make_skill_dir(tmp_path, "bar", "the bar skill")
        out = discover_skills([tmp_path])
        assert set(out.keys()) == {"foo", "bar"}
        assert out["foo"].description == "the foo skill"

    def test_discovers_flat_md_files(self, tmp_path):
        f = tmp_path / "quick.md"
        f.write_text(
            "---\nname: quick\ndescription: a flat skill\n---\nbody"
        )
        out = discover_skills([tmp_path])
        assert "quick" in out

    def test_user_root_overrides_shipped_root(self, tmp_path):
        shipped = tmp_path / "shipped"
        user = tmp_path / "user"
        self._make_skill_dir(shipped, "foo", "shipped version")
        self._make_skill_dir(user, "foo", "user override")
        out = discover_skills([shipped, user])
        assert out["foo"].description == "user override"

    def test_env_override_paths(self, tmp_path, monkeypatch):
        d1 = tmp_path / "a"
        d2 = tmp_path / "b"
        self._make_skill_dir(d1, "skill_a", "from a")
        self._make_skill_dir(d2, "skill_b", "from b")
        monkeypatch.setenv("JARVIS_SKILLS_PATHS", f"{d1}:{d2}")
        # discover_skills with default roots reads the env var.
        out = discover_skills()
        assert "skill_a" in out and "skill_b" in out

    def test_missing_root_silently_skipped(self, tmp_path):
        # /tmp/does-not-exist-xyz123 just isn't there — must not raise.
        out = discover_skills([tmp_path / "does-not-exist-xyz123"])
        assert out == {}

    def test_invalid_skill_file_skipped(self, tmp_path):
        # Make one valid + one broken (no frontmatter); broken should
        # be skipped, valid should still be picked up.
        d_valid = tmp_path / "valid"
        d_valid.mkdir()
        (d_valid / "SKILL.md").write_text(
            "---\nname: valid\ndescription: ok\n---\nbody"
        )
        d_broken = tmp_path / "broken"
        d_broken.mkdir()
        (d_broken / "SKILL.md").write_text("just text no frontmatter")
        out = discover_skills([tmp_path])
        assert "valid" in out
        assert "broken" not in out


# ── Registry ───────────────────────────────────────────────────────


class TestRegistry:
    def test_load_skills_populates_singleton(self, tmp_path, monkeypatch):
        d = tmp_path / "one"
        d.mkdir()
        (d / "SKILL.md").write_text(
            "---\nname: one\ndescription: first\n---\nbody"
        )
        monkeypatch.setenv("JARVIS_SKILLS_PATHS", str(tmp_path))

        # Re-import the module to pick up the new env. Easier: call
        # load_skills() with explicit roots so we don't fight the
        # module-singleton state.
        reg = load_skills([tmp_path])
        assert "one" in reg.names()
        assert len(reg) == 1

    def test_registry_iter_and_get(self, tmp_path):
        for n in ("a", "b", "c"):
            d = tmp_path / n
            d.mkdir()
            (d / "SKILL.md").write_text(
                f"---\nname: {n}\ndescription: {n}-desc\n---\nbody-{n}"
            )
        reg = load_skills([tmp_path])
        assert set(reg.names()) == {"a", "b", "c"}
        assert reg.get("b").description == "b-desc"
        assert reg.get("missing") is None
        # __iter__ works
        items = list(reg)
        assert {s.name for s in items} == {"a", "b", "c"}


# ── Bundled skills sanity ──────────────────────────────────────────


class TestBundledSkills:
    """The two skills shipped in src/voice-agent/skills/ must parse
    successfully. Acts as a parse-time CI check on the bundled files."""

    def test_git_status_skill_parses(self):
        path = Path(__file__).parent.parent / "skills" / "git-status" / "SKILL.md"
        assert path.exists(), f"bundled skill missing: {path}"
        sk = _parse_skill_file(path)
        assert sk is not None
        assert sk.name == "git-status"
        assert "git" in sk.body.lower()

    def test_system_stats_skill_parses(self):
        path = Path(__file__).parent.parent / "skills" / "system-stats" / "SKILL.md"
        assert path.exists(), f"bundled skill missing: {path}"
        sk = _parse_skill_file(path)
        assert sk is not None
        assert sk.name == "system-stats"
