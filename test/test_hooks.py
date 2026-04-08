"""Tests for src/hooks/manager.py — hook matching, command execution, new events, HTTP hooks."""

import os
import sys
import json
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.hooks.manager import HooksManager, HookResult, HOOK_EVENTS, BLOCKING_EVENTS


class TestHooksNoHooks(unittest.TestCase):
    """Behavior when no hooks are configured."""

    def test_no_hooks_allows_all(self):
        hm = HooksManager()
        result = hm.run_pre_tool_use("bash", {"command": "rm -rf /"})
        self.assertTrue(result.allowed)

        result = hm.run_post_tool_use("bash", {}, "some output")
        self.assertTrue(result.allowed)

        result = hm.run_stop()
        self.assertTrue(result.allowed)

    def test_has_hooks_false(self):
        hm = HooksManager()
        self.assertFalse(hm.has_hooks)

    def test_new_events_allow_when_empty(self):
        hm = HooksManager()
        self.assertTrue(hm.run_post_tool_use_failure("bash", {}, "error").allowed)
        self.assertTrue(hm.run_permission_denied("bash", {}, "denied").allowed)
        self.assertTrue(hm.run_notification("hello").allowed)
        self.assertTrue(hm.run_session_start().allowed)
        self.assertTrue(hm.run_session_end().allowed)


class TestMatcher(unittest.TestCase):
    """Tests for the _matches() method (now regex-based)."""

    def setUp(self):
        self.hm = HooksManager()

    def test_matcher_exact(self):
        self.assertTrue(self.hm._matches("bash", "bash"))
        self.assertFalse(self.hm._matches("bash", "edit_file"))

    def test_matcher_pipe_pattern(self):
        """Pipe-separated regex should match any alternative."""
        self.assertTrue(self.hm._matches("Edit|Write", "Edit"))
        self.assertTrue(self.hm._matches("Edit|Write", "Write"))
        self.assertFalse(self.hm._matches("Edit|Write", "bash"))

    def test_matcher_regex_wildcard(self):
        """Regex .* should match tool name patterns."""
        self.assertTrue(self.hm._matches("web_.*", "web_search"))
        self.assertTrue(self.hm._matches("web_.*", "web_fetch"))
        self.assertFalse(self.hm._matches("web_.*", "bash"))

    def test_matcher_prefix_wildcard_fallback(self):
        """Old-style web_* patterns should still work via fallback."""
        # This falls back to the simple pattern matching
        self.assertTrue(self.hm._matches("mcp__.*", "mcp__github__list"))

    def test_matcher_empty_tool(self):
        """Empty tool_name matches everything (for Stop/Session events)."""
        self.assertTrue(self.hm._matches("anything", ""))


class TestIfFilter(unittest.TestCase):
    """Tests for the if-filter fine-grained matching."""

    def setUp(self):
        self.hm = HooksManager()

    def test_if_filter_matches(self):
        self.assertTrue(self.hm._matches_if("bash(git *)", "bash", {"command": "git status"}))

    def test_if_filter_no_match(self):
        self.assertFalse(self.hm._matches_if("bash(git *)", "bash", {"command": "rm -rf /"}))

    def test_if_filter_wrong_tool(self):
        self.assertFalse(self.hm._matches_if("bash(git *)", "edit_file", {"command": "git"}))

    def test_if_filter_file_pattern(self):
        self.assertTrue(self.hm._matches_if("edit_file(*.py)", "edit_file", {"path": "main.py"}))
        self.assertFalse(self.hm._matches_if("edit_file(*.py)", "edit_file", {"path": "main.js"}))

    def test_no_if_filter(self):
        self.assertTrue(self.hm._matches_if("", "bash", {}))


class TestCommandHooks(unittest.TestCase):
    """Tests for command-type hook execution using mocked subprocess."""

    def setUp(self):
        self.hm = HooksManager()

    @patch("src.hooks.manager.subprocess.run")
    def test_command_hook_allow(self, mock_run):
        """Exit code 0 should allow the tool call."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        self.hm._config.events["PreToolUse"] = [{
            "matcher": "bash",
            "type": "command",
            "command": "echo ok",
            "timeout": 5,
        }]

        result = self.hm.run_pre_tool_use("bash", {"command": "ls"})
        self.assertTrue(result.allowed)
        mock_run.assert_called_once()

    @patch("src.hooks.manager.subprocess.run")
    def test_command_hook_block(self, mock_run):
        """Exit code 2 should block the tool call."""
        mock_run.return_value = MagicMock(returncode=2, stdout="", stderr="Blocked by policy")

        self.hm._config.events["PreToolUse"] = [{
            "matcher": "bash",
            "type": "command",
            "command": "check_policy.sh",
            "timeout": 5,
        }]

        result = self.hm.run_pre_tool_use("bash", {"command": "rm -rf /"})
        self.assertFalse(result.allowed)
        self.assertIn("Blocked", result.message)

    @patch("src.hooks.manager.subprocess.run")
    def test_command_hook_nonblocking_error(self, mock_run):
        """Exit code 1 (non-blocking error) should still allow."""
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="warning")

        self.hm._config.events["PreToolUse"] = [{
            "matcher": "bash",
            "type": "command",
            "command": "warn.sh",
            "timeout": 5,
        }]

        result = self.hm.run_pre_tool_use("bash", {"command": "ls"})
        self.assertTrue(result.allowed)

    @patch("src.hooks.manager.subprocess.run")
    def test_hook_timeout(self, mock_run):
        """A hanging hook should time out and allow."""
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="slow.sh", timeout=5)

        entry = {"matcher": "bash", "type": "command", "command": "slow.sh", "timeout": 5}

        result = self.hm._run_command_hook(entry, "bash", {"command": "ls"}, "PreToolUse", "")
        self.assertTrue(result.allowed)
        self.assertIn("timed out", result.message.lower())

    @patch("src.hooks.manager.subprocess.run")
    def test_hook_returns_modified_args(self, mock_run):
        """A hook returning JSON with tool_input should modify args."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"tool_input": {"command": "ls -la"}}),
            stderr="",
        )

        self.hm._config.events["PreToolUse"] = [{
            "matcher": "bash",
            "type": "command",
            "command": "sanitize.sh",
            "timeout": 5,
        }]

        result = self.hm.run_pre_tool_use("bash", {"command": "ls"})
        self.assertTrue(result.allowed)
        self.assertEqual(result.modified_args, {"command": "ls -la"})

    @patch("src.hooks.manager.subprocess.run")
    def test_hook_specific_output_deny(self, mock_run):
        """hookSpecificOutput with deny should block."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "Dangerous command",
                }
            }),
            stderr="",
        )

        self.hm._config.events["PreToolUse"] = [{
            "matcher": "bash",
            "type": "command",
            "command": "policy.sh",
            "timeout": 5,
        }]

        result = self.hm.run_pre_tool_use("bash", {"command": "rm -rf /"})
        self.assertFalse(result.allowed)
        self.assertIn("Dangerous", result.message)

    @patch("src.hooks.manager.subprocess.run")
    def test_hook_specific_output_updated_input(self, mock_run):
        """hookSpecificOutput with updatedInput should modify args."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "updatedInput": {"command": "ls --color"},
                }
            }),
            stderr="",
        )

        self.hm._config.events["PreToolUse"] = [{
            "matcher": "bash",
            "type": "command",
            "command": "rewrite.sh",
            "timeout": 5,
        }]

        result = self.hm.run_pre_tool_use("bash", {"command": "ls"})
        self.assertTrue(result.allowed)
        self.assertEqual(result.modified_args, {"command": "ls --color"})

    def test_matcher_skips_nonmatching(self):
        """Hooks with a non-matching matcher should be skipped entirely."""
        self.hm._config.events["PreToolUse"] = [{
            "matcher": "edit_file",
            "type": "command",
            "command": "should_not_run.sh",
            "timeout": 5,
        }]

        result = self.hm.run_pre_tool_use("bash", {"command": "ls"})
        self.assertTrue(result.allowed)

    def test_disabled_hook_skipped(self):
        """Disabled hooks should be skipped."""
        self.hm._config.events["PreToolUse"] = [{
            "matcher": "bash",
            "type": "command",
            "command": "should_not_run.sh",
            "enabled": False,
        }]

        result = self.hm.run_pre_tool_use("bash", {"command": "ls"})
        self.assertTrue(result.allowed)


class TestNewEvents(unittest.TestCase):
    """Tests for PostToolUseFailure, PermissionDenied, Notification, Session events."""

    def setUp(self):
        self.hm = HooksManager()

    @patch("src.hooks.manager.subprocess.run")
    def test_post_tool_use_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="logged", stderr="")

        self.hm._config.events["PostToolUseFailure"] = [{
            "matcher": "bash",
            "type": "command",
            "command": "log-error.sh",
            "timeout": 5,
        }]

        result = self.hm.run_post_tool_use_failure("bash", {"command": "bad"}, "exit 1")
        self.assertTrue(result.allowed)

    @patch("src.hooks.manager.subprocess.run")
    def test_permission_denied_hook(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        self.hm._config.events["PermissionDenied"] = [{
            "type": "command",
            "command": "notify-denied.sh",
            "timeout": 5,
        }]

        result = self.hm.run_permission_denied("bash", {"command": "rm /"}, "not allowed")
        self.assertTrue(result.allowed)

    @patch("src.hooks.manager.subprocess.run")
    def test_notification_hook(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        self.hm._config.events["Notification"] = [{
            "type": "command",
            "command": "notify.sh",
            "timeout": 5,
        }]

        result = self.hm.run_notification("task complete", "completion")
        self.assertTrue(result.allowed)

    @patch("src.hooks.manager.subprocess.run")
    def test_session_start_hook(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        self.hm._config.events["SessionStart"] = [{
            "type": "command",
            "command": "startup.sh",
            "timeout": 5,
        }]

        result = self.hm.run_session_start()
        self.assertTrue(result.allowed)


class TestRuntimeHooks(unittest.TestCase):
    """Tests for add_hook / remove_hook / list_hooks."""

    def setUp(self):
        self.hm = HooksManager()

    def test_add_hook(self):
        ok = self.hm.add_hook("PreToolUse", "check.sh", matcher="bash")
        self.assertTrue(ok)
        self.assertTrue(self.hm.has_hooks)
        self.assertEqual(len(self.hm._config.events["PreToolUse"]), 1)

    def test_add_hook_normalized_event(self):
        ok = self.hm.add_hook("pre_tool_use", "check.sh")
        self.assertTrue(ok)
        self.assertEqual(len(self.hm._config.events["PreToolUse"]), 1)

    def test_add_hook_invalid_event(self):
        ok = self.hm.add_hook("invalid_event", "check.sh")
        self.assertFalse(ok)

    def test_remove_hook(self):
        self.hm.add_hook("PreToolUse", "check.sh")
        self.hm.add_hook("PreToolUse", "audit.sh")
        ok = self.hm.remove_hook("PreToolUse", "check.sh")
        self.assertTrue(ok)
        self.assertEqual(len(self.hm._config.events["PreToolUse"]), 1)

    def test_remove_all_hooks_for_event(self):
        self.hm.add_hook("PreToolUse", "a.sh")
        self.hm.add_hook("PreToolUse", "b.sh")
        ok = self.hm.remove_hook("PreToolUse")
        self.assertTrue(ok)
        self.assertEqual(len(self.hm._config.events["PreToolUse"]), 0)

    def test_list_hooks(self):
        self.hm.add_hook("PreToolUse", "check.sh", matcher="bash")
        self.hm.add_hook("PostToolUse", "lint.sh")
        hooks = self.hm.list_hooks()
        self.assertEqual(len(hooks), 2)
        self.assertEqual(hooks[0]["event"], "PreToolUse")
        self.assertEqual(hooks[0]["command"], "check.sh")
        self.assertEqual(hooks[0]["matcher"], "bash")
        self.assertEqual(hooks[1]["event"], "PostToolUse")

    def test_list_hooks_empty(self):
        hooks = self.hm.list_hooks()
        self.assertEqual(len(hooks), 0)


class TestHooksSummary(unittest.TestCase):
    """Test the summary() method."""

    def test_summary_empty(self):
        hm = HooksManager()
        s = hm.summary()
        self.assertEqual(s["PreToolUse"], 0)
        self.assertEqual(s["PostToolUse"], 0)
        self.assertEqual(s["Stop"], 0)
        self.assertEqual(s["total"], 0)
        self.assertFalse(s["skill_hooks_active"])

    def test_summary_with_hooks(self):
        hm = HooksManager()
        hm._config.events["PreToolUse"] = [{"command": "a"}, {"command": "b"}]
        hm._config.events["PostToolUse"] = [{"command": "c"}]
        s = hm.summary()
        self.assertEqual(s["PreToolUse"], 2)
        self.assertEqual(s["PostToolUse"], 1)
        self.assertEqual(s["total"], 3)

    def test_skill_hooks(self):
        hm = HooksManager()
        hm.set_skill_hooks({"PreToolUse": [{"command": "x"}]})
        self.assertTrue(hm.has_hooks)
        self.assertTrue(hm.summary()["skill_hooks_active"])
        hm.clear_skill_hooks()
        self.assertFalse(hm.has_hooks)


class TestNestedHooksFormat(unittest.TestCase):
    """Test JARVIS-style nested hooks format."""

    @patch("src.hooks.manager.subprocess.run")
    def test_nested_hooks_key(self, mock_run):
        """Support matcher + nested hooks array format."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        hm = HooksManager()
        hm._config.events["PreToolUse"] = [{
            "matcher": "bash",
            "hooks": [
                {"type": "command", "command": "check1.sh", "timeout": 5},
                {"type": "command", "command": "check2.sh", "timeout": 5},
            ]
        }]

        result = hm.run_pre_tool_use("bash", {"command": "ls"})
        self.assertTrue(result.allowed)
        self.assertEqual(mock_run.call_count, 2)


class TestHookConstants(unittest.TestCase):
    """Test that constants are correct."""

    def test_all_events_present(self):
        # Core original events
        original = {
            "PreToolUse", "PostToolUse", "PostToolUseFailure",
            "PermissionDenied", "Notification", "Stop",
            "SessionStart", "SessionEnd",
            "SubagentStart", "SubagentStop",
            "CwdChanged", "FileChanged", "ContextCompacted",
        }
        # New events added from OpenClaw review
        new_events = {
            "BeforeModelResolve", "BeforePromptBuild",
            "LLMInput", "LLMOutput",
            "AgentStart", "AgentEnd",
            "CompactionStart", "CompactionEnd",
            "MemoryRead", "MemoryWrite",
            "PluginLoad", "PluginUnload",
            "SkillInvoke", "SkillComplete",
        }
        expected = original | new_events
        self.assertTrue(expected.issubset(set(HOOK_EVENTS)),
                        f"Missing events: {expected - set(HOOK_EVENTS)}")

    def test_blocking_events(self):
        self.assertIn("PreToolUse", BLOCKING_EVENTS)
        self.assertIn("Stop", BLOCKING_EVENTS)
        self.assertIn("SubagentStart", BLOCKING_EVENTS)
        self.assertIn("SubagentStop", BLOCKING_EVENTS)
        self.assertNotIn("PostToolUse", BLOCKING_EVENTS)
        self.assertNotIn("Notification", BLOCKING_EVENTS)
        self.assertNotIn("CwdChanged", BLOCKING_EVENTS)
        self.assertNotIn("FileChanged", BLOCKING_EVENTS)
        self.assertNotIn("ContextCompacted", BLOCKING_EVENTS)


class TestEventNormalization(unittest.TestCase):
    """Test _normalize_event maps old names to new."""

    def setUp(self):
        self.hm = HooksManager()

    def test_already_correct(self):
        self.assertEqual(self.hm._normalize_event("PreToolUse"), "PreToolUse")

    def test_snake_case(self):
        self.assertEqual(self.hm._normalize_event("pre_tool_use"), "PreToolUse")
        self.assertEqual(self.hm._normalize_event("post_tool_use"), "PostToolUse")

    def test_legacy_names(self):
        self.assertEqual(self.hm._normalize_event("pre_command"), "PreToolUse")
        self.assertEqual(self.hm._normalize_event("post_command"), "PostToolUse")
        self.assertEqual(self.hm._normalize_event("on_error"), "PostToolUseFailure")
        self.assertEqual(self.hm._normalize_event("on_startup"), "SessionStart")
        self.assertEqual(self.hm._normalize_event("on_shutdown"), "SessionEnd")


if __name__ == "__main__":
    unittest.main()
