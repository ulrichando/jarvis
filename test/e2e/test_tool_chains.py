"""E2E tests for tool chain outcomes — direct execute_tool calls, no mock LLM."""

import os
import sys
import tempfile
import shutil
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from test.e2e.base import E2EBase
from src.agent.tools import execute_tool
from src.agent.loop import _is_tool_failure


class TestToolChains(E2EBase):

    def test_write_then_read_chain(self):
        path = self._tmp_path("chain_test.txt")
        execute_tool("write_file", {"path": path, "content": "hello"})
        result = execute_tool("read_file", {"path": path})
        self.assertIn("hello", result)

    def test_edit_preserves_other_content(self):
        path = self._write_tmp("edit_test.txt", "line one\nline two\nline three\n")
        # edit_file uses "path" key (not "file_path")
        execute_tool("edit_file", {
            "path": path,
            "old_string": "line two",
            "new_string": "line TWO updated",
        })
        # Read raw file content to bypass read_file line numbering
        with open(path) as f:
            content = f.read()
        self.assertIn("line one", content)
        self.assertIn("line TWO updated", content)
        self.assertIn("line three", content)
        self.assertNotIn("line two\n", content)

    def test_bash_to_read_chain(self):
        out_path = self._tmp_path("out.txt")
        execute_tool("bash", {"command": f"echo 'test output' > {out_path}"})
        result = execute_tool("read_file", {"path": out_path})
        self.assertIn("test output", result)

    def test_grep_returns_matches(self):
        self._write_tmp("marker_file.txt", "MARKER: important line\nsome other line\nanother line\n")
        # Default output_mode is "files_with_matches" — request "content" to see matches
        result = execute_tool("grep", {"pattern": "MARKER:", "path": self.tmp, "output_mode": "content"})
        self.assertIn("MARKER", result)

    def test_glob_finds_files(self):
        for i in range(3):
            self._write_tmp(f"file{i}.py", f"# python file {i}")
        for i in range(2):
            self._write_tmp(f"file{i}.txt", f"text file {i}")

        result = execute_tool("glob", {"pattern": "*.py", "path": self.tmp})
        py_count = result.count(".py")
        # Verify .py files found and no .txt files in result
        self.assertGreaterEqual(py_count, 3, f"Expected at least 3 .py matches, got: {result!r}")
        # txt files should not appear in a *.py glob
        self.assertNotIn(".txt", result)

    def test_readonly_bash_allows_ls(self):
        result = execute_tool("bash", {"command": "ls /tmp"}, readonly=True)
        self.assertNotIn("BLOCKED", result, f"ls should be allowed in readonly mode, got: {result!r}")

    def test_readonly_bash_blocks_rm(self):
        # rm on a nonexistent path to avoid actual deletion, but still test the block
        result = execute_tool("bash", {"command": "rm /tmp/jarvis_e2e_nonexistent_file"}, readonly=True)
        self.assertIn("BLOCKED", result, f"rm should be blocked in readonly mode, got: {result!r}")

    def test_tool_result_failure_detection(self):
        result = execute_tool("bash", {"command": "cat /nonexistent_jarvis_test_abc123"})
        is_fail = _is_tool_failure(result)
        self.assertTrue(is_fail, f"Expected failure detection for bad cat, got result: {result!r}")
