"""Tests for brain/agent/tools.py — path validation, tool execution, readonly mode."""

import os
import sys
import tempfile
import shutil
import unittest

# Ensure project root is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from brain.agent.tools import _validate_path, execute_tool


class TestValidatePath(unittest.TestCase):
    """Path validation security tests."""

    def test_validate_path_blocks_sensitive(self):
        """Sensitive system files must be blocked."""
        for path in ["/etc/shadow", "/etc/passwd", "/etc/sudoers"]:
            valid, err = _validate_path(path)
            self.assertFalse(valid, f"{path} should be blocked")
            self.assertIn("protected", err.lower())

        # SSH keys
        ssh_key = os.path.expanduser("~/.ssh/id_rsa")
        valid, err = _validate_path(ssh_key)
        self.assertFalse(valid, "~/.ssh/id_rsa should be blocked")

    def test_validate_path_allows_home(self):
        """Normal paths under the home directory should be allowed."""
        home_path = os.path.expanduser("~/Documents/test.txt")
        valid, err = _validate_path(home_path)
        self.assertTrue(valid, f"Home path should be allowed: {err}")

        tmp_path = "/tmp/some_file.txt"
        valid, err = _validate_path(tmp_path)
        self.assertTrue(valid, f"/tmp path should be allowed: {err}")

    def test_validate_path_blocks_traversal(self):
        """Path traversal attempts must be caught after realpath resolution."""
        # This resolves to /etc/shadow regardless of the leading path
        traversal = os.path.expanduser("~/../../etc/shadow")
        valid, err = _validate_path(traversal)
        self.assertFalse(valid, "Traversal to /etc/shadow should be blocked")

    def test_validate_path_write_blocks_etc(self):
        """Write access to /etc should be blocked even for non-sensitive files."""
        valid, err = _validate_path("/etc/hostname", write=True)
        self.assertFalse(valid, "Writing to /etc should be blocked")
        self.assertIn("blocked", err.lower())


class TestExecRead(unittest.TestCase):
    """Tests for the read_file tool."""

    def test_exec_read_nonexistent(self):
        result = execute_tool("read_file", {"path": "/tmp/__jarvis_nonexistent_12345__"})
        self.assertIn("not found", result.lower())

    def test_exec_read_directory(self):
        """Reading a directory should return a listing."""
        with tempfile.TemporaryDirectory() as td:
            # Create some files
            open(os.path.join(td, "alpha.txt"), "w").close()
            open(os.path.join(td, "beta.txt"), "w").close()
            result = execute_tool("read_file", {"path": td})
            self.assertIn("alpha.txt", result)
            self.assertIn("beta.txt", result)
            self.assertIn("Directory listing", result)


class TestExecWrite(unittest.TestCase):
    """Tests for the write_file tool."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="jarvis_test_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_exec_write_creates_file(self):
        path = os.path.join(self.tmpdir, "output.txt")
        result = execute_tool("write_file", {"path": path, "content": "hello world\n"})
        self.assertIn("Wrote", result)
        self.assertTrue(os.path.exists(path))
        with open(path) as f:
            self.assertEqual(f.read(), "hello world\n")


class TestExecEdit(unittest.TestCase):
    """Tests for the edit_file tool."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="jarvis_test_")
        self.filepath = os.path.join(self.tmpdir, "editable.txt")
        with open(self.filepath, "w") as f:
            f.write("line one\nline two\nline three\n")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_exec_edit_unique_match(self):
        result = execute_tool("edit_file", {
            "path": self.filepath,
            "old_string": "line two",
            "new_string": "line TWO replaced",
        })
        self.assertIn("successfully", result.lower())
        with open(self.filepath) as f:
            content = f.read()
        self.assertIn("line TWO replaced", content)
        self.assertNotIn("line two\n", content)

    def test_exec_edit_no_match(self):
        result = execute_tool("edit_file", {
            "path": self.filepath,
            "old_string": "this string does not exist",
            "new_string": "replacement",
        })
        self.assertIn("not found", result.lower())


class TestExecBash(unittest.TestCase):
    """Tests for the bash tool."""

    def test_exec_bash_timeout(self):
        result = execute_tool("bash", {"command": "sleep 60", "timeout": 1})
        self.assertIn("timed out", result.lower())


class TestExecuteTool(unittest.TestCase):
    """Tests for the top-level execute_tool dispatcher."""

    def test_execute_tool_unknown(self):
        result = execute_tool("nonexistent_tool_xyz", {})
        self.assertIn("Unknown tool", result)

    def test_readonly_blocks_writes(self):
        """In readonly mode, write_file and edit_file must be blocked."""
        result = execute_tool("write_file", {"path": "/tmp/x", "content": "x"}, readonly=True)
        self.assertIn("BLOCKED", result)

        result = execute_tool("edit_file", {
            "path": "/tmp/x", "old_string": "a", "new_string": "b",
        }, readonly=True)
        self.assertIn("BLOCKED", result)

    def test_readonly_blocks_bash_write_commands(self):
        """Readonly mode should block non-whitelisted bash commands."""
        result = execute_tool("bash", {"command": "rm -rf /tmp/foo"}, readonly=True)
        self.assertIn("BLOCKED", result)

    def test_readonly_allows_bash_read_commands(self):
        """Readonly mode should allow whitelisted read commands."""
        result = execute_tool("bash", {"command": "ls /tmp"}, readonly=True)
        self.assertNotIn("BLOCKED", result)


if __name__ == "__main__":
    unittest.main()
