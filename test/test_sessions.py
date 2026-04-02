"""Tests for brain/sessions.py — session creation, persistence, search."""

import os
import sys
import shutil
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from brain.sessions import Session, SessionManager, _validate_session_id


class TestSessionCreation(unittest.TestCase):
    """Session dataclass tests."""

    def test_create_session(self):
        s = Session()
        self.assertTrue(len(s.id) > 0)
        self.assertEqual(s.mode, "normal")
        self.assertIsInstance(s.messages, list)
        self.assertEqual(len(s.messages), 0)

    def test_session_id_validation(self):
        """Invalid IDs must raise ValueError."""
        for bad_id in ["", "a" * 65, "has spaces", "path/../traversal", "semi;colon"]:
            with self.assertRaises(ValueError, msg=f"Should reject {bad_id!r}"):
                _validate_session_id(bad_id)

    def test_session_id_path_traversal(self):
        """Path traversal IDs must be rejected."""
        with self.assertRaises(ValueError):
            _validate_session_id("../../../etc")
        with self.assertRaises(ValueError):
            Session(session_id="../../etc/passwd")

    def test_session_add_message(self):
        s = Session()
        s.add_message("user", "hello")
        s.add_message("assistant", "hi there")
        self.assertEqual(len(s.messages), 2)
        self.assertEqual(s.messages[0]["role"], "user")
        self.assertEqual(s.messages[1]["content"], "hi there")

    def test_session_display_name_auto(self):
        """Display name should auto-generate from first user message."""
        s = Session()
        s.add_message("user", "What is the weather like today?")
        self.assertIn("weather", s.display_name.lower())

    def test_session_display_name_fallback(self):
        """With no name and no messages, display_name should use the ID prefix."""
        s = Session()
        self.assertEqual(s.display_name, s.id[:8])

    def test_session_display_name_explicit(self):
        """Explicit name should take priority."""
        s = Session(name="my-session")
        self.assertEqual(s.display_name, "my-session")


class TestSessionManager(unittest.TestCase):
    """SessionManager persistence and lookup tests."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="jarvis_test_sessions_")
        self.db_path = os.path.join(self.tmpdir, "sessions.db")
        self.mgr = SessionManager(db_path=self.db_path)

    def tearDown(self):
        self.mgr.close()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_session_manager_new(self):
        s = self.mgr.new(name="test-session")
        self.assertEqual(s.name, "test-session")
        self.assertIsNotNone(self.mgr.current)
        self.assertEqual(self.mgr.current.id, s.id)

    def test_session_manager_save_load(self):
        s = self.mgr.new(name="persist-test")
        s.add_message("user", "hello persist")
        self.mgr.save_current()

        # Load in a fresh manager instance
        mgr2 = SessionManager(db_path=self.db_path)
        loaded = mgr2.get(s.id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.name, "persist-test")
        self.assertEqual(len(loaded.messages), 1)
        self.assertEqual(loaded.messages[0]["content"], "hello persist")
        mgr2.close()

    def test_session_manager_find_by_name(self):
        self.mgr.new(name="alpha")
        self.mgr.new(name="beta")
        found = self.mgr.find("alpha")
        self.assertIsNotNone(found)
        self.assertEqual(found.name, "alpha")

    def test_session_manager_find_by_prefix(self):
        s = self.mgr.new(name="prefix-test")
        prefix = s.id[:6]
        found = self.mgr.find(prefix)
        self.assertIsNotNone(found)
        self.assertEqual(found.id, s.id)

    def test_session_manager_list(self):
        self.mgr.new(name="one")
        self.mgr.new(name="two")
        self.mgr.new(name="three")
        listing = self.mgr.list_sessions()
        self.assertEqual(len(listing), 3)
        # Most recent first
        self.assertEqual(listing[0]["name"], "three")

    def test_session_add_message_via_manager(self):
        self.mgr.new(name="msg-test")
        self.mgr.add_message("user", "first")
        self.mgr.add_message("assistant", "reply")
        self.assertEqual(len(self.mgr.current.messages), 2)


if __name__ == "__main__":
    unittest.main()
