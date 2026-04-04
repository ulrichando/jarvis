"""Tests for the enhanced permission system: modes, rules, matcher, denial tracking."""

import json
import tempfile
from pathlib import Path

import pytest

from src.permissions import (
    PermissionLevel,
    PermissionMode,
    PermissionRule,
    PermissionMatcher,
    PermissionManager,
)


# ---------------------------------------------------------------------------
# PermissionMode
# ---------------------------------------------------------------------------

class TestPermissionMode:
    def test_enum_values(self):
        assert PermissionMode.DEFAULT.value == "default"
        assert PermissionMode.BYPASS.value == "bypass"
        assert PermissionMode.ACCEPT_EDITS.value == "accept_edits"
        assert PermissionMode.DENY_ALL.value == "deny_all"
        assert PermissionMode.PLAN.value == "plan"


# ---------------------------------------------------------------------------
# PermissionRule
# ---------------------------------------------------------------------------

class TestPermissionRule:
    def test_defaults(self):
        r = PermissionRule(tool_name="bash")
        assert r.rule_content == ""
        assert r.behavior == "allow"
        assert r.source == "user"

    def test_specificity_wildcard(self):
        assert PermissionRule("*").specificity == 0

    def test_specificity_tool_only(self):
        assert PermissionRule("bash").specificity == 2

    def test_specificity_tool_plus_content(self):
        assert PermissionRule("bash", "git *").specificity == 3

    def test_specificity_wildcard_with_content(self):
        assert PermissionRule("*", "*.py").specificity == 1


# ---------------------------------------------------------------------------
# PermissionMatcher — parsing
# ---------------------------------------------------------------------------

class TestParseRuleString:
    def test_allow_tool_with_pattern(self):
        r = PermissionMatcher.parse_rule_string("allow:bash(git *)")
        assert r.tool_name == "bash"
        assert r.rule_content == "git *"
        assert r.behavior == "allow"

    def test_deny_dangerous(self):
        r = PermissionMatcher.parse_rule_string("deny:bash(rm -rf *)")
        assert r.behavior == "deny"
        assert r.rule_content == "rm -rf *"

    def test_allow_tool_no_content(self):
        r = PermissionMatcher.parse_rule_string("allow:read_file")
        assert r.tool_name == "read_file"
        assert r.rule_content == ""

    def test_deny_wildcard(self):
        r = PermissionMatcher.parse_rule_string("deny:*")
        assert r.tool_name == "*"
        assert r.behavior == "deny"

    def test_ask_with_path_pattern(self):
        r = PermissionMatcher.parse_rule_string("ask:write_file(/etc/*)")
        assert r.tool_name == "write_file"
        assert r.rule_content == "/etc/*"
        assert r.behavior == "ask"

    def test_source_passed_through(self):
        r = PermissionMatcher.parse_rule_string("allow:bash", source="cli")
        assert r.source == "cli"

    def test_invalid_no_colon(self):
        with pytest.raises(ValueError, match="missing behavior prefix"):
            PermissionMatcher.parse_rule_string("bash")

    def test_invalid_behavior(self):
        with pytest.raises(ValueError, match="Invalid behavior"):
            PermissionMatcher.parse_rule_string("yolo:bash")


# ---------------------------------------------------------------------------
# PermissionMatcher — matching
# ---------------------------------------------------------------------------

class TestPermissionMatcherCheck:
    def setup_method(self):
        self.matcher = PermissionMatcher([
            PermissionRule("bash", "git *", "allow"),
            PermissionRule("bash", "rm -rf *", "deny"),
            PermissionRule("bash", "", "ask"),
            PermissionRule("read_file", "", "allow"),
            PermissionRule("*", "", "deny"),
        ])

    def test_specific_allow(self):
        b, _ = self.matcher.check("bash", {"command": "git status"})
        assert b == "allow"

    def test_specific_deny(self):
        b, _ = self.matcher.check("bash", {"command": "rm -rf /"})
        assert b == "deny"

    def test_tool_fallback_ask(self):
        b, _ = self.matcher.check("bash", {"command": "echo hello"})
        assert b == "ask"

    def test_other_tool_explicit_allow(self):
        b, _ = self.matcher.check("read_file", {"path": "/tmp/x"})
        assert b == "allow"

    def test_wildcard_deny(self):
        b, _ = self.matcher.check("unknown_tool", {})
        assert b == "deny"

    def test_no_rules_returns_ask(self):
        m = PermissionMatcher()
        b, reason = m.check("bash", {"command": "ls"})
        assert b == "ask"
        assert "no matching rule" in reason

    def test_edit_file_path_match(self):
        m = PermissionMatcher([PermissionRule("edit_file", "/home/*", "allow")])
        b, _ = m.check("edit_file", {"path": "/home/ulrich/test.py"})
        assert b == "allow"
        b, _ = m.check("edit_file", {"path": "/etc/passwd"})
        assert b == "ask"

    def test_search_files_pattern_match(self):
        m = PermissionMatcher([PermissionRule("search_files", "TODO*", "allow")])
        b, _ = m.check("search_files", {"pattern": "TODO fixme"})
        assert b == "allow"

    def test_add_and_remove_rule(self):
        m = PermissionMatcher()
        m.add_rule(PermissionRule("bash", "ls *", "allow"))
        assert len(m.rules) == 1
        assert m.remove_rule("bash", "ls *") is True
        assert len(m.rules) == 0
        assert m.remove_rule("bash", "ls *") is False


# ---------------------------------------------------------------------------
# PermissionManager — modes
# ---------------------------------------------------------------------------

class TestPermissionManagerModes:
    def test_bypass_allows_everything(self):
        pm = PermissionManager(mode=PermissionMode.BYPASS)
        ok, _ = pm.check("bash", {"command": "rm -rf /"})
        assert ok is True

    def test_plan_allows_readonly(self):
        pm = PermissionManager(mode=PermissionMode.PLAN)
        ok, _ = pm.check("read_file", {"path": "/tmp/x"})
        assert ok is True
        ok, _ = pm.check("search_files", {"pattern": "test"})
        assert ok is True
        ok, _ = pm.check("think", {})
        assert ok is True

    def test_plan_denies_writes(self):
        pm = PermissionManager(mode=PermissionMode.PLAN)
        ok, _ = pm.check("bash", {"command": "ls"})
        assert ok is False
        ok, _ = pm.check("write_file", {"path": "/tmp/x"})
        assert ok is False
        ok, _ = pm.check("edit_file", {"path": "/tmp/x"})
        assert ok is False

    def test_accept_edits_auto_accepts_edits(self):
        pm = PermissionManager(mode=PermissionMode.ACCEPT_EDITS)
        ok, _ = pm.check("edit_file", {"path": "/tmp/x"})
        assert ok is True
        ok, _ = pm.check("write_file", {"path": "/tmp/x"})
        assert ok is True

    def test_accept_edits_normal_for_others(self):
        pm = PermissionManager(mode=PermissionMode.ACCEPT_EDITS)
        # bash should still pass (normal flow, FULL level)
        ok, _ = pm.check("bash", {"command": "ls"})
        assert ok is True

    def test_deny_all_blocks_without_allowlist(self):
        pm = PermissionManager(mode=PermissionMode.DENY_ALL)
        ok, _ = pm.check("bash", {"command": "ls"})
        assert ok is False

    def test_deny_all_allows_allowlisted(self):
        pm = PermissionManager(mode=PermissionMode.DENY_ALL)
        pm.add_rule(PermissionRule("bash", "git *", "allow"))
        ok, _ = pm.check("bash", {"command": "git status"})
        assert ok is True


# ---------------------------------------------------------------------------
# Denial tracking
# ---------------------------------------------------------------------------

class TestDenialTracking:
    def test_record_and_count(self):
        pm = PermissionManager()
        assert pm.get_denial_count("bash") == 0
        pm.record_denial("bash")
        assert pm.get_denial_count("bash") == 1
        pm.record_denial("bash")
        assert pm.get_denial_count("bash") == 2

    def test_should_stop_asking(self):
        pm = PermissionManager()
        for _ in range(2):
            pm.record_denial("bash")
        assert pm.should_stop_asking("bash") is False
        pm.record_denial("bash")
        assert pm.should_stop_asking("bash") is True

    def test_custom_threshold(self):
        pm = PermissionManager()
        pm.record_denial("bash")
        assert pm.should_stop_asking("bash", threshold=1) is True

    def test_reset(self):
        pm = PermissionManager()
        pm.record_denial("bash")
        pm.reset_denial_counts()
        assert pm.get_denial_count("bash") == 0


# ---------------------------------------------------------------------------
# Rule loading from settings
# ---------------------------------------------------------------------------

class TestLoadRules:
    def test_load_from_settings_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            jarvis_dir = Path(tmpdir) / ".jarvis"
            jarvis_dir.mkdir()
            settings = {
                "permissions": [
                    "allow:bash(git *)",
                    "deny:bash(rm -rf *)",
                    {"tool": "read_file", "behavior": "allow"},
                ]
            }
            (jarvis_dir / "settings.json").write_text(json.dumps(settings))

            pm = PermissionManager()
            count = pm.load_rules_from_settings(project_dir=tmpdir)
            assert count == 3
            assert len(pm.matcher.rules) == 3

    def test_load_missing_files(self):
        pm = PermissionManager()
        count = pm.load_rules_from_settings(project_dir="/nonexistent")
        assert count == 0

    def test_load_no_permissions_key(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            jarvis_dir = Path(tmpdir) / ".jarvis"
            jarvis_dir.mkdir()
            (jarvis_dir / "settings.json").write_text("{}")

            pm = PermissionManager()
            count = pm.load_rules_from_settings(project_dir=tmpdir)
            assert count == 0


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

class TestSummary:
    def test_summary_includes_new_fields(self):
        pm = PermissionManager(mode=PermissionMode.DEFAULT)
        pm.add_rule_string("deny:bash(rm *)")
        pm.record_denial("bash")
        s = pm.summary()
        assert s["mode"] == "default"
        assert len(s["rules"]) == 1
        assert s["rules"][0]["behavior"] == "deny"
        assert s["denial_counts"]["bash"] == 1
