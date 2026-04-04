"""Tests for brain/permissions.py — role-based access control."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.permissions import PermissionLevel, PermissionManager


class TestPermissionLevel(unittest.TestCase):
    """Verify enum ordering."""

    def test_level_ordering(self):
        self.assertLess(PermissionLevel.READ_ONLY, PermissionLevel.STANDARD)
        self.assertLess(PermissionLevel.STANDARD, PermissionLevel.FULL)
        self.assertLess(PermissionLevel.FULL, PermissionLevel.DANGEROUS_FULL)


class TestPermissionManager(unittest.TestCase):
    """PermissionManager check/deny/allow tests."""

    def test_default_level_full(self):
        pm = PermissionManager()
        self.assertEqual(pm.level, PermissionLevel.FULL)

    def test_readonly_blocks_write(self):
        pm = PermissionManager(level=PermissionLevel.READ_ONLY)
        allowed, reason = pm.check("write_file")
        self.assertFalse(allowed)
        self.assertIn("requires", reason.lower())

    def test_readonly_allows_read(self):
        pm = PermissionManager(level=PermissionLevel.READ_ONLY)
        allowed, reason = pm.check("read_file")
        self.assertTrue(allowed, f"read_file should be allowed: {reason}")

    def test_standard_allows_write(self):
        pm = PermissionManager(level=PermissionLevel.STANDARD)
        allowed, reason = pm.check("write_file")
        self.assertTrue(allowed, f"write_file should be allowed at STANDARD: {reason}")

    def test_deny_tool(self):
        pm = PermissionManager()
        pm.deny_tool("bash")
        allowed, reason = pm.check("bash")
        self.assertFalse(allowed)
        self.assertIn("denied", reason.lower())

    def test_deny_prefix(self):
        pm = PermissionManager()
        pm.deny_prefix("web_")
        allowed, _ = pm.check("web_search")
        self.assertFalse(allowed)
        allowed, _ = pm.check("web_fetch")
        self.assertFalse(allowed)
        # Non-matching tool should still be allowed
        allowed, _ = pm.check("read_file")
        self.assertTrue(allowed)

    def test_allow_after_deny(self):
        pm = PermissionManager()
        pm.deny_tool("bash")
        allowed, _ = pm.check("bash")
        self.assertFalse(allowed)

        pm.allow_tool("bash")
        allowed, _ = pm.check("bash")
        self.assertTrue(allowed)

    def test_get_allowed_tools_filtering(self):
        pm = PermissionManager(level=PermissionLevel.READ_ONLY)
        tools = ["read_file", "write_file", "edit_file", "search_files", "think"]
        allowed = pm.get_allowed_tools(tools)
        self.assertIn("read_file", allowed)
        self.assertIn("search_files", allowed)
        self.assertNotIn("write_file", allowed)
        self.assertNotIn("edit_file", allowed)

    def test_summary(self):
        pm = PermissionManager()
        pm.deny_tool("bash")
        pm.deny_prefix("web_")
        s = pm.summary()
        self.assertEqual(s["level"], "FULL")
        self.assertIn("bash", s["denied_tools"])
        self.assertIn("web_", s["denied_prefixes"])


if __name__ == "__main__":
    unittest.main()
