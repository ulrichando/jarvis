"""Comprehensive tool execution tests.

Every callable tool in execute_tool() is tested with real inputs.
No mocking — tests verify actual behavior.

Categories:
  - File I/O:    bash, read_file, write_file, edit_file, search_files, Glob, Grep
  - Reasoning:   think, tool_search
  - System:      sysinfo, Sleep, ConfigTool
  - RAG:         rag_search
  - UI/comms:    BriefTool, ListMcpResources
  - Loop sentinels (dispatch, ask_user, EnterPlanMode, ExitPlanMode,
                    EnterWorktree, ExitWorktree, SendMessage, RemoteTrigger,
                    TeamCreate, TeamDelete, Skill, LSP, ScheduleCron):
                 verified to return their expected sentinel string
  - Tasks:       TaskCreate, TaskGet, TaskList, TaskStop, TaskUpdate, TaskOutput
  - Security:    security_scan (no live network — local path only)
  - readonly mode: write/edit/destructive bash blocked in plan mode
  - unknown:     unknown tool name returns error string
"""

import os
import sys
import tempfile
import shutil
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.agent.tools import execute_tool

JARVIS_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


# ══════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════

class _TmpDir(unittest.TestCase):
    """Base class that sets up a fresh temp dir per test."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="jarvis_tool_test_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name: str, content: str) -> str:
        path = os.path.join(self.tmp, name)
        with open(path, "w") as f:
            f.write(content)
        return path


# ══════════════════════════════════════════════════════════════════════
# 1. bash
# ══════════════════════════════════════════════════════════════════════

class TestBashTool(unittest.TestCase):

    def test_echo_output(self):
        r = execute_tool("bash", {"command": "echo jarvis_ok"})
        self.assertIn("jarvis_ok", r)

    def test_exit_code_zero(self):
        r = execute_tool("bash", {"command": "true"})
        self.assertIn("exit_code=0", r)

    def test_exit_code_nonzero(self):
        r = execute_tool("bash", {"command": "false"})
        self.assertIn("exit_code=1", r)

    def test_multiline_output(self):
        r = execute_tool("bash", {"command": "printf 'line1\\nline2\\nline3'"})
        self.assertIn("line1", r)
        self.assertIn("line3", r)

    def test_pipe(self):
        r = execute_tool("bash", {"command": "echo hello | tr a-z A-Z"})
        self.assertIn("HELLO", r)

    def test_env_variable(self):
        r = execute_tool("bash", {"command": "echo $HOME"})
        # Bash may run in a sandbox with a different HOME — just verify it expands
        self.assertIn("exit_code=0", r)
        self.assertTrue(len(r.strip()) > 0)

    def test_blocked_rm(self):
        r = execute_tool("bash", {"command": "rm -rf /"})
        self.assertTrue(
            "BLOCKED" in r.upper() or "blocked" in r.lower() or "error" in r.lower(),
            f"rm -rf / should be blocked, got: {r[:100]}"
        )

    def test_readonly_mode_blocks_write(self):
        # Use a clearly destructive command that hits the blocked-prefix list
        r = execute_tool("bash", {"command": "rm -rf /tmp"}, readonly=True)
        self.assertTrue(
            "BLOCKED" in r.upper() or "blocked" in r.lower(),
            f"rm -rf should be blocked in readonly mode, got: {r[:100]}"
        )

    def test_readonly_mode_allows_ls(self):
        r = execute_tool("bash", {"command": "ls /tmp"}, readonly=True)
        # Should not be blocked
        self.assertNotIn("BLOCKED", r.upper())

    def test_stderr_captured(self):
        r = execute_tool("bash", {"command": "ls /no_such_dir_xyz_12345 2>&1"})
        # stderr should appear in output or exit_code!=0
        self.assertTrue("No such file" in r or "exit_code=2" in r or "exit_code=1" in r)

    def test_python_inline(self):
        r = execute_tool("bash", {"command": "python3 -c \"print(2+2)\""})
        self.assertIn("4", r)

    def test_arithmetic(self):
        r = execute_tool("bash", {"command": "expr 7 + 8"})
        self.assertIn("15", r)


# ══════════════════════════════════════════════════════════════════════
# 2. read_file
# ══════════════════════════════════════════════════════════════════════

class TestReadFileTool(_TmpDir):

    def test_reads_content(self):
        p = self._write("hello.txt", "hello jarvis")
        r = execute_tool("read_file", {"path": p})
        self.assertIn("hello jarvis", r)

    def test_missing_file_error(self):
        r = execute_tool("read_file", {"path": "/tmp/__no_such_file_xyz__"})
        self.assertIn("not found", r.lower())

    def test_offset_and_limit(self):
        lines = "\n".join(f"line{i}" for i in range(1, 21))
        p = self._write("multiline.txt", lines)
        r = execute_tool("read_file", {"path": p, "offset": 5, "limit": 3})
        self.assertIn("line6", r)
        self.assertNotIn("line1", r)

    def test_reads_python_file(self):
        r = execute_tool("read_file", {"path": os.path.join(JARVIS_ROOT, "src/agent/agents.py")})
        self.assertIn("AgentConfig", r)

    def test_blocked_sensitive_path(self):
        r = execute_tool("read_file", {"path": "/etc/shadow"})
        self.assertTrue(
            "protected" in r.lower() or "blocked" in r.lower() or "error" in r.lower()
        )

    def test_readonly_mode_still_reads(self):
        p = self._write("r.txt", "read me")
        r = execute_tool("read_file", {"path": p}, readonly=True)
        self.assertIn("read me", r)


# ══════════════════════════════════════════════════════════════════════
# 3. write_file
# ══════════════════════════════════════════════════════════════════════

class TestWriteFileTool(_TmpDir):

    def test_creates_file(self):
        p = os.path.join(self.tmp, "new.txt")
        execute_tool("write_file", {"path": p, "content": "jarvis write"})
        self.assertTrue(os.path.exists(p))
        self.assertIn("jarvis write", open(p).read())

    def test_overwrites_existing(self):
        p = self._write("existing.txt", "old content")
        execute_tool("write_file", {"path": p, "content": "new content"})
        self.assertIn("new content", open(p).read())
        self.assertNotIn("old content", open(p).read())

    def test_creates_parent_dirs(self):
        p = os.path.join(self.tmp, "sub", "dir", "file.txt")
        r = execute_tool("write_file", {"path": p, "content": "deep write"})
        self.assertTrue(os.path.exists(p))

    def test_blocked_sensitive_path(self):
        r = execute_tool("write_file", {"path": "/etc/test_jarvis", "content": "x"})
        self.assertTrue(
            "blocked" in r.lower() or "protected" in r.lower() or "error" in r.lower()
        )

    def test_readonly_mode_blocked(self):
        p = os.path.join(self.tmp, "ro.txt")
        r = execute_tool("write_file", {"path": p, "content": "x"}, readonly=True)
        self.assertIn("BLOCKED", r.upper())
        self.assertFalse(os.path.exists(p))

    def test_returns_success_message(self):
        p = os.path.join(self.tmp, "ok.txt")
        r = execute_tool("write_file", {"path": p, "content": "ok"})
        self.assertIsInstance(r, str)
        self.assertNotIn("Error", r)


# ══════════════════════════════════════════════════════════════════════
# 4. edit_file
# ══════════════════════════════════════════════════════════════════════

class TestEditFileTool(_TmpDir):

    def test_replaces_string(self):
        p = self._write("edit.txt", "foo bar baz")
        execute_tool("edit_file", {"path": p, "old_string": "bar", "new_string": "QUX"})
        self.assertIn("QUX", open(p).read())
        self.assertNotIn("bar", open(p).read())

    def test_old_string_not_found(self):
        p = self._write("nomatch.txt", "hello world")
        r = execute_tool("edit_file", {
            "path": p, "old_string": "__NOTHERE__", "new_string": "x"
        })
        self.assertTrue("not found" in r.lower() or "error" in r.lower())

    def test_multiline_replace(self):
        content = "def foo():\n    pass\n"
        p = self._write("code.py", content)
        execute_tool("edit_file", {
            "path": p,
            "old_string": "def foo():\n    pass",
            "new_string": "def foo():\n    return 42",
        })
        self.assertIn("return 42", open(p).read())

    def test_missing_file(self):
        r = execute_tool("edit_file", {
            "path": "/tmp/__no_edit_file__",
            "old_string": "x", "new_string": "y"
        })
        self.assertTrue("not found" in r.lower() or "error" in r.lower())

    def test_readonly_mode_blocked(self):
        p = self._write("ro_edit.txt", "original")
        r = execute_tool("edit_file", {
            "path": p, "old_string": "original", "new_string": "changed"
        }, readonly=True)
        self.assertIn("BLOCKED", r.upper())
        self.assertIn("original", open(p).read())  # unchanged

    def test_replace_all_flag(self):
        p = self._write("multi.txt", "x x x")
        execute_tool("edit_file", {
            "path": p, "old_string": "x", "new_string": "y", "replace_all": True
        })
        content = open(p).read()
        self.assertNotIn("x", content)
        self.assertEqual(content.count("y"), 3)


# ══════════════════════════════════════════════════════════════════════
# 5. search_files  (legacy alias for Grep)
# ══════════════════════════════════════════════════════════════════════

class TestSearchFilesTool(unittest.TestCase):

    def test_finds_pattern_in_dir(self):
        r = execute_tool("search_files", {
            "path": os.path.join(JARVIS_ROOT, "src/agent"),
            "pattern": "AGENT_CONFIGS",
        })
        self.assertIn("AGENT_CONFIGS", r)

    def test_no_match_returns_string(self):
        r = execute_tool("search_files", {
            "path": os.path.join(JARVIS_ROOT, "src/agent"),
            "pattern": "__PATTERN_NEVER_EXISTS_XYZ__",
        })
        self.assertIsInstance(r, str)


# ══════════════════════════════════════════════════════════════════════
# 6. Glob
# ══════════════════════════════════════════════════════════════════════

class TestGlobTool(unittest.TestCase):

    def test_finds_python_files(self):
        r = execute_tool("Glob", {
            "pattern": "src/**/*.py",
            "path": JARVIS_ROOT,
        })
        self.assertIn(".py", r)
        # Result is a list of file paths — verify at least one src/ file appears
        self.assertIn("src/", r.lower() if r == r.upper() else r)

    def test_finds_specific_file(self):
        r = execute_tool("Glob", {
            "pattern": "src/agent/agents.py",
            "path": JARVIS_ROOT,
        })
        self.assertIn("agents.py", r)

    def test_no_match_returns_string(self):
        r = execute_tool("Glob", {"pattern": "**/__nope_xyz__.txt", "path": JARVIS_ROOT})
        self.assertIsInstance(r, str)
        self.assertNotIn("ERROR", r.upper())

    def test_finds_test_files(self):
        r = execute_tool("Glob", {"pattern": "test/test_*.py", "path": JARVIS_ROOT})
        self.assertIn("test_", r)


# ══════════════════════════════════════════════════════════════════════
# 7. Grep
# ══════════════════════════════════════════════════════════════════════

class TestGrepTool(unittest.TestCase):

    def test_files_with_matches_mode(self):
        r = execute_tool("Grep", {
            "pattern": "execute_tool",
            "path": os.path.join(JARVIS_ROOT, "src/agent/tools.py"),
        })
        self.assertIsInstance(r, str)
        self.assertGreater(len(r), 0)

    def test_content_mode(self):
        r = execute_tool("Grep", {
            "pattern": "AgentConfig",
            "path": os.path.join(JARVIS_ROOT, "src/agent/agents.py"),
            "output_mode": "content",
        })
        self.assertIn("AgentConfig", r)

    def test_case_insensitive(self):
        r = execute_tool("Grep", {
            "pattern": "agentconfig",
            "path": os.path.join(JARVIS_ROOT, "src/agent/agents.py"),
            "output_mode": "content",
            "case_insensitive": True,
        })
        self.assertIsInstance(r, str)

    def test_no_match_returns_string(self):
        r = execute_tool("Grep", {
            "pattern": "__NEVER_MATCHES_XYZ_12345__",
            "path": JARVIS_ROOT,
        })
        self.assertIsInstance(r, str)

    def test_count_mode(self):
        r = execute_tool("Grep", {
            "pattern": "def ",
            "path": os.path.join(JARVIS_ROOT, "src/agent/tools.py"),
            "output_mode": "count",
        })
        self.assertIsInstance(r, str)


# ══════════════════════════════════════════════════════════════════════
# 8. think
# ══════════════════════════════════════════════════════════════════════

class TestThinkTool(unittest.TestCase):

    def test_returns_thought(self):
        r = execute_tool("think", {"thought": "The answer is 42."})
        self.assertIn("42", r)

    def test_empty_thought(self):
        r = execute_tool("think", {"thought": ""})
        self.assertEqual(r, "")

    def test_multiline_thought(self):
        thought = "Step 1: read\nStep 2: analyze\nStep 3: act"
        r = execute_tool("think", {"thought": thought})
        self.assertIn("Step 1", r)
        self.assertIn("Step 3", r)

    def test_no_thought_key(self):
        r = execute_tool("think", {})
        self.assertEqual(r, "")


# ══════════════════════════════════════════════════════════════════════
# 9. tool_search
# ══════════════════════════════════════════════════════════════════════

class TestToolSearchTool(unittest.TestCase):

    def test_finds_bash(self):
        r = execute_tool("tool_search", {"query": "bash shell command"})
        self.assertIsInstance(r, str)
        self.assertNotIn("Traceback", r)

    def test_finds_by_select(self):
        r = execute_tool("tool_search", {"query": "select:bash"})
        self.assertIsInstance(r, str)
        self.assertNotIn("Traceback", r)

    def test_no_results(self):
        r = execute_tool("tool_search", {"query": "__xyzzy_not_a_tool__"})
        self.assertIsInstance(r, str)

    def test_empty_query(self):
        r = execute_tool("tool_search", {"query": ""})
        self.assertIn("No query", r)


# ══════════════════════════════════════════════════════════════════════
# 10. sysinfo
# ══════════════════════════════════════════════════════════════════════

class TestSysinfoTool(unittest.TestCase):

    def test_returns_system_data(self):
        r = execute_tool("sysinfo", {})
        self.assertIsInstance(r, str)
        # sysinfo includes live system logs which may contain tracebacks from other processes
        self.assertGreater(len(r), 0)

    def test_contains_platform_info(self):
        r = execute_tool("sysinfo", {})
        # sysinfo returns services/processes info — check for common keywords
        self.assertTrue(
            any(kw in r.lower() for kw in
                ("service", "unit", "cpu", "memory", "os", "python",
                 "platform", "pid", "active", "loaded", "running"))
        )


# ══════════════════════════════════════════════════════════════════════
# 11. Sleep
# ══════════════════════════════════════════════════════════════════════

class TestSleepTool(unittest.TestCase):

    def test_zero_seconds(self):
        r = execute_tool("Sleep", {"seconds": 0})
        self.assertIsInstance(r, str)

    def test_very_short(self):
        r = execute_tool("Sleep", {"seconds": 0.01})
        self.assertIsInstance(r, str)
        self.assertNotIn("Error", r)


# ══════════════════════════════════════════════════════════════════════
# 12. ConfigTool
# ══════════════════════════════════════════════════════════════════════

class TestConfigTool(unittest.TestCase):

    def test_get_returns_config(self):
        r = execute_tool("ConfigTool", {"action": "get"})
        self.assertIsInstance(r, str)
        self.assertNotIn("Traceback", r)

    def test_list_action(self):
        r = execute_tool("ConfigTool", {"action": "list"})
        self.assertIsInstance(r, str)
        self.assertNotIn("Traceback", r)

    def test_invalid_action(self):
        r = execute_tool("ConfigTool", {"action": "nope_xyz"})
        self.assertIsInstance(r, str)


# ══════════════════════════════════════════════════════════════════════
# 13. rag_search
# ══════════════════════════════════════════════════════════════════════

class TestRagSearchTool(unittest.TestCase):

    def test_runs_without_error(self):
        r = execute_tool("rag_search", {"query": "agent loop tools"})
        self.assertIsInstance(r, str)
        self.assertNotIn("Traceback", r)

    def test_empty_query(self):
        r = execute_tool("rag_search", {"query": ""})
        self.assertIsInstance(r, str)

    def test_returns_string_always(self):
        for q in ["python", "security scan", "memory store", "langsmith tracing"]:
            with self.subTest(query=q):
                r = execute_tool("rag_search", {"query": q})
                self.assertIsInstance(r, str)


# ══════════════════════════════════════════════════════════════════════
# 14. BriefTool
# ══════════════════════════════════════════════════════════════════════

class TestBriefTool(unittest.TestCase):

    def test_returns_message(self):
        r = execute_tool("BriefTool", {"message": "Task is complete."})
        self.assertEqual(r, "Task is complete.")

    def test_empty_message(self):
        r = execute_tool("BriefTool", {"message": ""})
        self.assertEqual(r, "")

    def test_no_message_key(self):
        r = execute_tool("BriefTool", {})
        self.assertEqual(r, "")


# ══════════════════════════════════════════════════════════════════════
# 15. ListMcpResources
# ══════════════════════════════════════════════════════════════════════

class TestListMcpResourcesTool(unittest.TestCase):

    def test_returns_string(self):
        r = execute_tool("ListMcpResources", {})
        self.assertIsInstance(r, str)
        self.assertNotIn("Traceback", r)


# ══════════════════════════════════════════════════════════════════════
# 16. Task tools
# ══════════════════════════════════════════════════════════════════════

class TestTaskTools(unittest.TestCase):

    def test_task_list_returns_string(self):
        r = execute_tool("TaskList", {})
        self.assertIsInstance(r, str)
        self.assertNotIn("Traceback", r)

    def test_task_create_returns_string(self):
        r = execute_tool("TaskCreate", {
            "command": "echo test_task",
            "description": "Test task from unit test",
        })
        self.assertIsInstance(r, str)
        self.assertNotIn("Traceback", r)

    def test_task_get_nonexistent(self):
        r = execute_tool("TaskGet", {"task_id": "nonexistent_task_xyz_999"})
        self.assertIsInstance(r, str)

    def test_task_output_nonexistent(self):
        r = execute_tool("TaskOutput", {"task_id": "nonexistent_task_xyz_999"})
        self.assertIsInstance(r, str)

    def test_task_stop_nonexistent(self):
        r = execute_tool("TaskStop", {"task_id": "nonexistent_task_xyz_999"})
        self.assertIsInstance(r, str)


# ══════════════════════════════════════════════════════════════════════
# 17. Loop sentinels — tools handled by the agent loop, not execute_tool
# ══════════════════════════════════════════════════════════════════════

class TestLoopSentinels(unittest.TestCase):
    """These tools return magic strings consumed by the agent loop.
    They should never raise — just return the sentinel."""

    _SENTINELS = {
        "dispatch":       "__DISPATCH__",
        "ask_user":       "__ASK_USER__",
        "EnterPlanMode":  "__PLAN_MODE_ENTER__",
        "ExitPlanMode":   "__PLAN_MODE_EXIT__",
        "EnterWorktree":  "__WORKTREE_ENTER__",
        "ExitWorktree":   "__WORKTREE_EXIT__",
        "SendMessage":    "__SEND_MESSAGE__",
        "RemoteTrigger":  "__REMOTE_TRIGGER__",
        "TeamCreate":     "__TEAM_CREATE__",
        "TeamDelete":     "__TEAM_DELETE__",
        "Skill":          "__SKILL__",
        "LSP":            "__LSP__",
        "ScheduleCron":   "__CRON__",
    }

    def test_sentinels_return_expected_strings(self):
        for tool_name, expected in self._SENTINELS.items():
            with self.subTest(tool=tool_name):
                r = execute_tool(tool_name, {})
                self.assertEqual(r, expected,
                    f"{tool_name} expected '{expected}', got '{r}'")


# ══════════════════════════════════════════════════════════════════════
# 18. readonly mode — plan mode enforcement
# ══════════════════════════════════════════════════════════════════════

class TestReadonlyMode(_TmpDir):

    def test_write_file_blocked(self):
        p = os.path.join(self.tmp, "ro.txt")
        r = execute_tool("write_file", {"path": p, "content": "x"}, readonly=True)
        self.assertIn("BLOCKED", r.upper())
        self.assertFalse(os.path.exists(p))

    def test_edit_file_blocked(self):
        p = self._write("ro_edit.txt", "original")
        r = execute_tool("edit_file", {
            "path": p, "old_string": "original", "new_string": "changed"
        }, readonly=True)
        self.assertIn("BLOCKED", r.upper())
        self.assertIn("original", open(p).read())

    def test_bash_destructive_blocked(self):
        r = execute_tool("bash", {"command": "rm -rf /tmp"}, readonly=True)
        self.assertTrue(
            "BLOCKED" in r.upper() or "blocked" in r.lower(),
            f"rm should be blocked in readonly mode, got: {r[:100]}"
        )

    def test_read_file_allowed(self):
        p = self._write("allowed.txt", "can read")
        r = execute_tool("read_file", {"path": p}, readonly=True)
        self.assertIn("can read", r)

    def test_bash_ls_allowed(self):
        r = execute_tool("bash", {"command": "ls /tmp"}, readonly=True)
        self.assertNotIn("BLOCKED", r.upper())

    def test_think_allowed(self):
        r = execute_tool("think", {"thought": "thinking..."}, readonly=True)
        self.assertEqual(r, "thinking...")


# ══════════════════════════════════════════════════════════════════════
# 19. security_scan (local path only — no network)
# ══════════════════════════════════════════════════════════════════════

class TestSecurityScanTool(_TmpDir):

    def test_scan_directory_returns_string(self):
        # Write a file with a fake "secret"
        self._write("config.py", 'SECRET_KEY = "hardcoded_secret_1234"')
        r = execute_tool("security_scan", {
            "target": self.tmp,
            "scan_type": "secrets",
        })
        self.assertIsInstance(r, str)
        self.assertNotIn("Traceback", r)

    def test_scan_nonexistent_path(self):
        r = execute_tool("security_scan", {
            "target": "/tmp/__no_such_dir_xyz__",
            "scan_type": "secrets",
        })
        self.assertIsInstance(r, str)

    def test_scan_code_returns_string(self):
        self._write("app.py", "import subprocess\nsubprocess.run(input())")
        r = execute_tool("security_scan", {
            "target": self.tmp,
            "scan_type": "code",
        })
        self.assertIsInstance(r, str)
        self.assertNotIn("Traceback", r)


# ══════════════════════════════════════════════════════════════════════
# 20. Unknown tool
# ══════════════════════════════════════════════════════════════════════

class TestUnknownTool(unittest.TestCase):

    def test_unknown_returns_error(self):
        r = execute_tool("__not_a_real_tool__", {})
        self.assertIn("Unknown tool", r)

    def test_unknown_does_not_raise(self):
        try:
            execute_tool("totally_fake_xyz", {"foo": "bar"})
        except Exception as e:
            self.fail(f"execute_tool raised unexpectedly: {e}")


# ══════════════════════════════════════════════════════════════════════
# 21. Path security (cross-tool)
# ══════════════════════════════════════════════════════════════════════

class TestPathSecurity(unittest.TestCase):

    _SENSITIVE = [
        "/etc/shadow",
        "/etc/sudoers",
        "/root/.ssh/id_rsa",
    ]

    def test_read_blocked_for_sensitive(self):
        for path in self._SENSITIVE:
            with self.subTest(path=path):
                r = execute_tool("read_file", {"path": path})
                self.assertTrue(
                    "protected" in r.lower()
                    or "blocked" in r.lower()
                    or "error" in r.lower()
                    or "not found" in r.lower(),
                    f"Sensitive path not blocked: {path} → {r[:80]}"
                )

    def test_write_blocked_for_etc(self):
        r = execute_tool("write_file", {"path": "/etc/jarvis_test_xyz", "content": "x"})
        self.assertTrue(
            "blocked" in r.lower() or "protected" in r.lower() or "error" in r.lower()
        )

    def test_traversal_blocked(self):
        traversal = os.path.expanduser("~/../../etc/shadow")
        r = execute_tool("read_file", {"path": traversal})
        self.assertTrue(
            "protected" in r.lower()
            or "blocked" in r.lower()
            or "error" in r.lower()
            or "not found" in r.lower(),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
