"""Tests for pipeline/agent_authoring.py — author/validate/render/discover
user subagent definitions written to ~/.jarvis/agents/<name>.md.

Isolation: every test sets JARVIS_AGENTS_PATHS at a tmp_path (single root) or
"<project_tmp>:<user_tmp>" (two roots, user last = writable), exactly like the
skills_authoring tests use JARVIS_SKILLS_PATHS — nothing escapes to the real
~/.jarvis/agents/ during the run.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_VOICE_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_VOICE_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_VOICE_AGENT_ROOT))

os.environ.setdefault("GROQ_API_KEY", "test-key-for-init")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")

from pipeline import agent_authoring as aa  # noqa: E402


def _read(p) -> str:
    return Path(p).read_text(encoding="utf-8")


# ── render / escape ─────────────────────────────────────────────────────


class TestRender:
    def test_minimal_round_trips(self):
        md = aa.render_agent_md("triage-bot", "Use for triage.", "You triage incoming reports carefully.")
        assert md.startswith("---\nname: triage-bot\n")
        assert 'description: "Use for triage."' in md
        assert md.rstrip().endswith("You triage incoming reports carefully.")
        # No tools/model lines when not provided.
        assert "\ntools:" not in md
        assert "\nmodel:" not in md

    def test_tools_string_and_list(self):
        md_s = aa.render_agent_md("a-b-c", "d", "body body body body", tools="Read, Grep , WebSearch")
        assert "tools: Read, Grep, WebSearch" in md_s
        md_l = aa.render_agent_md("a-b-c", "d", "body body body body", tools=["Read", "Bash"])
        assert "tools: Read, Bash" in md_l

    def test_all_tools_omits_line(self):
        md = aa.render_agent_md("a-b-c", "d", "body body body body", tools=["*"])
        assert "\ntools:" not in md

    def test_model_line(self):
        md = aa.render_agent_md("a-b-c", "d", "body body body body", model="inherit")
        assert "model: inherit" in md

    def test_description_escaping_matches_cli(self):
        # Newlines → literal \n, quotes escaped, backslashes doubled — the
        # exact transform the CLI's formatAgentAsMarkdown applies.
        md = aa.render_agent_md("a-b-c", 'line1\nline2 "q" \\x', "body body body body")
        line = next(ln for ln in md.splitlines() if ln.startswith("description:"))
        assert "\\n" in line          # newline became backslash-n
        assert '\\"q\\"' in line       # quotes escaped
        assert "\\\\x" in line          # backslash doubled
        assert "\n" not in line[len("description: "):]  # value stays single physical line


# ── validation ───────────────────────────────────────────────────────────


class TestValidateName:
    @pytest.mark.parametrize("name", ["abc", "a-b", "web-researcher", "Plan9", "x1y"])
    def test_valid(self, name):
        assert aa.validate_name(name) is None

    @pytest.mark.parametrize("name", ["", "ab", "-abc", "abc-", "bad name", "has.dot", "a/b", "x" * 51])
    def test_invalid(self, name):
        assert aa.validate_name(name) is not None


# ── create / discover ──────────────────────────────────────────────────────


class TestCreateDiscover:
    def test_creates_and_discovers(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JARVIS_AGENTS_PATHS", str(tmp_path))
        res = aa.create_user_agent(
            "release-notes-writer",
            "Use to draft release notes from a git log.",
            "You are a release-notes specialist. Summarize commits into crisp notes.",
        )
        assert res["ok"], res
        f = tmp_path / "release-notes-writer.md"
        assert f.exists()
        found = aa.find_agent("release-notes-writer")
        assert found is not None
        assert found["editable"] is True
        assert "release notes" in found["description"].lower()

    def test_bad_name_no_write(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JARVIS_AGENTS_PATHS", str(tmp_path))
        res = aa.create_user_agent("Bad Name", "desc here", "a long enough body string")
        assert not res["ok"]
        assert list(tmp_path.glob("*.md")) == []

    def test_short_body_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JARVIS_AGENTS_PATHS", str(tmp_path))
        res = aa.create_user_agent("tiny-body", "desc here", "too short")
        assert not res["ok"]
        assert "short" in res["error"].lower()

    def test_empty_description_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JARVIS_AGENTS_PATHS", str(tmp_path))
        res = aa.create_user_agent("no-desc", "", "a long enough body string here")
        assert not res["ok"]

    def test_duplicate_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JARVIS_AGENTS_PATHS", str(tmp_path))
        aa.create_user_agent("dup-agent", "desc here", "a long enough body string here")
        res = aa.create_user_agent("dup-agent", "desc here", "another long enough body here")
        assert not res["ok"]
        assert "already exists" in res["error"]

    @pytest.mark.parametrize("name", ["explore", "researcher", "code_reviewer", "plan"])
    def test_reserved_dispatch_name_rejected(self, name, tmp_path, monkeypatch):
        # code_reviewer fails the hyphen-only charset anyway, but the reserved
        # guard gives the clearer message; explore/researcher/plan pass the
        # charset and MUST be rejected by the reserved guard, not silently written.
        monkeypatch.setenv("JARVIS_AGENTS_PATHS", str(tmp_path))
        res = aa.create_user_agent(name, "desc here", "a long enough body string here")
        assert not res["ok"]
        assert list(tmp_path.glob("*.md")) == []


# ── edit / patch ───────────────────────────────────────────────────────────


class TestEdit:
    def _seed(self, tmp_path):
        aa.create_user_agent(
            "editme", "Original when-to-use.",
            "Original system prompt body that is plenty long.",
            tools="Read, Grep", model="inherit",
        )
        return tmp_path / "editme.md"

    def test_body_only_preserves_frontmatter_verbatim(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JARVIS_AGENTS_PATHS", str(tmp_path))
        f = self._seed(tmp_path)
        before_fm = _read(f).split("---")[1]  # frontmatter block
        res = aa.edit_user_agent("editme", "A brand new system prompt body, still long enough.")
        assert res["ok"], res
        after = _read(f)
        assert after.split("---")[1] == before_fm   # frontmatter untouched
        assert "brand new system prompt body" in after
        assert "Original system prompt body" not in after

    def test_edit_with_description_rerenders(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JARVIS_AGENTS_PATHS", str(tmp_path))
        self._seed(tmp_path)
        res = aa.edit_user_agent(
            "editme", "New long enough body content here.",
            description="A new when-to-use string.",
        )
        assert res["ok"], res
        found = aa.find_agent("editme")
        assert "new when-to-use" in found["description"].lower()

    def test_tools_change_requires_description(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JARVIS_AGENTS_PATHS", str(tmp_path))
        self._seed(tmp_path)
        res = aa.edit_user_agent("editme", "New long enough body content here.", tools="Read")
        assert not res["ok"]
        assert "description" in res["error"].lower()

    def test_edit_unknown_agent(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JARVIS_AGENTS_PATHS", str(tmp_path))
        res = aa.edit_user_agent("nope", "New long enough body content here.")
        assert not res["ok"]
        assert "No agent named" in res["error"]


# ── delete ─────────────────────────────────────────────────────────────────


class TestDelete:
    def test_delete_moves_to_trash(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JARVIS_AGENTS_PATHS", str(tmp_path))
        aa.create_user_agent("trashme", "desc here", "a long enough body string here")
        f = tmp_path / "trashme.md"
        assert f.exists()
        res = aa.delete_user_agent("trashme")
        assert res["ok"], res
        assert not f.exists()
        assert aa.find_agent("trashme") is None
        trash = Path(res["trashed_to"])
        assert trash.exists()
        assert trash.parent.name == ".agents-trash"

    def test_delete_unknown(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JARVIS_AGENTS_PATHS", str(tmp_path))
        res = aa.delete_user_agent("ghost")
        assert not res["ok"]


# ── project (read-only) agents ───────────────────────────────────────────────


class TestReadOnlyProjectAgents:
    def _two_roots(self, tmp_path, monkeypatch):
        project = tmp_path / "project"
        user = tmp_path / "user"
        project.mkdir()
        user.mkdir()
        monkeypatch.setenv("JARVIS_AGENTS_PATHS", f"{project}:{user}")
        return project, user

    def test_project_agent_is_read_only(self, tmp_path, monkeypatch):
        project, user = self._two_roots(tmp_path, monkeypatch)
        (project / "shipped-agent.md").write_text(
            '---\nname: shipped-agent\ndescription: "A shipped agent."\n---\n\nSystem prompt body long enough.\n'
        )
        found = aa.find_agent("shipped-agent")
        assert found is not None and found["editable"] is False
        assert not aa.edit_user_agent("shipped-agent", "New long enough body content here.")["ok"]
        assert not aa.delete_user_agent("shipped-agent")["ok"]

    def test_user_create_shadowing_project_name_flags_shadow(self, tmp_path, monkeypatch):
        project, user = self._two_roots(tmp_path, monkeypatch)
        (project / "dual.md").write_text(
            '---\nname: dual\ndescription: "Project version."\n---\n\nProject system prompt body long enough.\n'
        )
        res = aa.create_user_agent("dual", "User version.", "User system prompt body that is long enough.")
        assert res["ok"], res
        assert res["shadow"] is True
        # User root now owns the editable copy.
        assert (user / "dual.md").exists()
        assert aa.find_agent("dual")["editable"] is True
