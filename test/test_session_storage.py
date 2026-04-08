"""Tests for src/memory/session_storage.py — SessionEntry, SessionStorage,
flush, add_entry, and get_history.

All tests use tempfile directories so they are self-contained and isolated.
"""

import json
import os
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.memory.session_storage import SessionEntry, SessionStorage


# ---------------------------------------------------------------------------
# SessionEntry dataclass tests
# ---------------------------------------------------------------------------

class TestSessionEntry(unittest.TestCase):
    """Test SessionEntry construction, serialization, and deserialization."""

    def _make_entry(self, **overrides):
        defaults = dict(
            session_id="sess-001",
            timestamp=1700000000.0,
            role="user",
            content="Hello JARVIS",
            tool_name="",
            tool_args={},
            metadata={},
        )
        defaults.update(overrides)
        return SessionEntry(**defaults)

    # -- construction --------------------------------------------------------

    def test_basic_creation(self):
        e = self._make_entry()
        self.assertEqual(e.session_id, "sess-001")
        self.assertEqual(e.timestamp, 1700000000.0)
        self.assertEqual(e.role, "user")
        self.assertEqual(e.content, "Hello JARVIS")
        self.assertEqual(e.tool_name, "")
        self.assertEqual(e.tool_args, {})
        self.assertEqual(e.metadata, {})

    def test_tool_entry(self):
        e = self._make_entry(
            role="tool",
            tool_name="bash",
            tool_args={"command": "ls"},
            content="file1.txt\nfile2.txt",
        )
        self.assertEqual(e.role, "tool")
        self.assertEqual(e.tool_name, "bash")
        self.assertEqual(e.tool_args, {"command": "ls"})

    def test_metadata_dict(self):
        e = self._make_entry(metadata={"provider": "claude", "tokens": 150})
        self.assertEqual(e.metadata["provider"], "claude")
        self.assertEqual(e.metadata["tokens"], 150)

    # -- to_dict -------------------------------------------------------------

    def test_to_dict_keys(self):
        e = self._make_entry()
        d = e.to_dict()
        expected_keys = {"session_id", "timestamp", "role", "content",
                         "tool_name", "tool_args", "metadata"}
        self.assertEqual(set(d.keys()), expected_keys)

    def test_to_dict_values(self):
        e = self._make_entry(role="jarvis", content="I'm here.")
        d = e.to_dict()
        self.assertEqual(d["role"], "jarvis")
        self.assertEqual(d["content"], "I'm here.")

    def test_to_dict_is_plain_dict(self):
        """to_dict should return a plain dict serializable as JSON."""
        e = self._make_entry(tool_args={"x": [1, 2, 3]})
        d = e.to_dict()
        # Should not raise
        serialized = json.dumps(d)
        self.assertIsInstance(serialized, str)

    # -- from_dict -----------------------------------------------------------

    def test_from_dict_full(self):
        data = {
            "session_id": "s42",
            "timestamp": 1234567890.5,
            "role": "system",
            "content": "System init",
            "tool_name": "",
            "tool_args": {},
            "metadata": {"init": True},
        }
        e = SessionEntry.from_dict(data)
        self.assertEqual(e.session_id, "s42")
        self.assertEqual(e.timestamp, 1234567890.5)
        self.assertEqual(e.role, "system")
        self.assertEqual(e.content, "System init")
        self.assertEqual(e.metadata, {"init": True})

    def test_from_dict_missing_optional_fields(self):
        """from_dict should handle missing optional fields with defaults."""
        data = {
            "session_id": "s1",
            "timestamp": 100.0,
            "role": "user",
            "content": "hi",
        }
        e = SessionEntry.from_dict(data)
        self.assertEqual(e.tool_name, "")
        self.assertEqual(e.tool_args, {})
        self.assertEqual(e.metadata, {})

    def test_from_dict_completely_empty(self):
        """from_dict with empty dict should use all defaults."""
        e = SessionEntry.from_dict({})
        self.assertEqual(e.session_id, "")
        self.assertEqual(e.timestamp, 0.0)
        self.assertEqual(e.role, "")
        self.assertEqual(e.content, "")

    # -- roundtrip -----------------------------------------------------------

    def test_roundtrip_to_from_dict(self):
        """to_dict() -> from_dict() should preserve all fields."""
        original = self._make_entry(
            session_id="rt-session",
            timestamp=1700001234.567,
            role="tool",
            content="output from bash",
            tool_name="bash",
            tool_args={"command": "echo hello"},
            metadata={"exit_code": 0},
        )
        d = original.to_dict()
        restored = SessionEntry.from_dict(d)
        self.assertEqual(restored.session_id, original.session_id)
        self.assertEqual(restored.timestamp, original.timestamp)
        self.assertEqual(restored.role, original.role)
        self.assertEqual(restored.content, original.content)
        self.assertEqual(restored.tool_name, original.tool_name)
        self.assertEqual(restored.tool_args, original.tool_args)
        self.assertEqual(restored.metadata, original.metadata)

    def test_roundtrip_through_json(self):
        """Serialize to JSON string and back, verifying full fidelity."""
        original = self._make_entry(
            role="jarvis",
            content="Here is the answer.",
            metadata={"model": "gpt-4o", "latency_ms": 230},
        )
        json_str = json.dumps(original.to_dict())
        restored = SessionEntry.from_dict(json.loads(json_str))
        self.assertEqual(restored.role, original.role)
        self.assertEqual(restored.content, original.content)
        self.assertEqual(restored.metadata, original.metadata)


# ---------------------------------------------------------------------------
# SessionStorage tests
# ---------------------------------------------------------------------------

class TestSessionStorage(unittest.TestCase):
    """Test SessionStorage init, add_entry, flush, and get_history."""

    def _make_storage(self, tmpdir, session_id="test-session"):
        return SessionStorage(storage_dir=tmpdir, session_id=session_id)

    # -- initialization ------------------------------------------------------

    def test_init_creates_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage_dir = os.path.join(tmpdir, "sessions")
            _ = SessionStorage(storage_dir=storage_dir, session_id="s1")
            self.assertTrue(os.path.isdir(storage_dir))

    def test_init_custom_session_id(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            s = self._make_storage(tmpdir, session_id="custom-id")
            self.assertEqual(s.session_id, "custom-id")

    def test_init_auto_generates_session_id(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            s = SessionStorage(storage_dir=tmpdir)
            self.assertTrue(len(s.session_id) > 0)

    # -- add_entry -----------------------------------------------------------

    def test_add_entry_returns_session_entry(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            s = self._make_storage(tmpdir)
            entry = s.add_entry("user", "Hello")
            self.assertIsInstance(entry, SessionEntry)
            self.assertEqual(entry.role, "user")
            self.assertEqual(entry.content, "Hello")
            self.assertEqual(entry.session_id, "test-session")

    def test_add_entry_with_tool_info(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            s = self._make_storage(tmpdir)
            entry = s.add_entry(
                "tool", "file contents here",
                tool_name="read_file",
                tool_args={"path": "/tmp/test.txt"},
            )
            self.assertEqual(entry.tool_name, "read_file")
            self.assertEqual(entry.tool_args, {"path": "/tmp/test.txt"})

    def test_add_entry_with_metadata(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            s = self._make_storage(tmpdir)
            entry = s.add_entry("jarvis", "Response", metadata={"tokens": 42})
            self.assertEqual(entry.metadata, {"tokens": 42})

    def test_add_entry_timestamp_set(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            s = self._make_storage(tmpdir)
            before = time.time()
            entry = s.add_entry("user", "test")
            after = time.time()
            self.assertGreaterEqual(entry.timestamp, before)
            self.assertLessEqual(entry.timestamp, after)

    def test_add_multiple_entries(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            s = self._make_storage(tmpdir)
            s.add_entry("user", "Q1")
            s.add_entry("jarvis", "A1")
            s.add_entry("user", "Q2")
            self.assertEqual(len(s._entries), 3)
            self.assertEqual(len(s._pending_flush), 3)

    def test_add_entry_various_roles(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            s = self._make_storage(tmpdir)
            for role in ["user", "jarvis", "system", "tool"]:
                e = s.add_entry(role, f"msg from {role}")
                self.assertEqual(e.role, role)

    # -- flush ---------------------------------------------------------------

    def test_flush_writes_jsonl(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            s = self._make_storage(tmpdir)
            s.add_entry("user", "Hello")
            s.add_entry("jarvis", "Hi there")
            s.flush()

            path = os.path.join(tmpdir, "test-session.jsonl")
            self.assertTrue(os.path.exists(path))
            with open(path, "r") as f:
                lines = f.readlines()
            self.assertEqual(len(lines), 2)

            entry1 = json.loads(lines[0])
            self.assertEqual(entry1["role"], "user")
            self.assertEqual(entry1["content"], "Hello")

            entry2 = json.loads(lines[1])
            self.assertEqual(entry2["role"], "jarvis")
            self.assertEqual(entry2["content"], "Hi there")

    def test_flush_clears_pending(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            s = self._make_storage(tmpdir)
            s.add_entry("user", "msg")
            self.assertEqual(len(s._pending_flush), 1)
            s.flush()
            self.assertEqual(len(s._pending_flush), 0)
            # In-memory entries remain
            self.assertEqual(len(s._entries), 1)

    def test_flush_empty_is_noop(self):
        """Flushing with no pending entries should not create a file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            s = self._make_storage(tmpdir)
            s.flush()
            path = os.path.join(tmpdir, "test-session.jsonl")
            self.assertFalse(os.path.exists(path))

    def test_flush_appends_on_second_call(self):
        """Multiple flushes should append to the same file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            s = self._make_storage(tmpdir)
            s.add_entry("user", "first")
            s.flush()
            s.add_entry("jarvis", "second")
            s.flush()

            path = os.path.join(tmpdir, "test-session.jsonl")
            with open(path, "r") as f:
                lines = f.readlines()
            self.assertEqual(len(lines), 2)
            self.assertEqual(json.loads(lines[0])["content"], "first")
            self.assertEqual(json.loads(lines[1])["content"], "second")

    def test_flush_idempotent(self):
        """Calling flush twice in a row without new entries should be safe."""
        with tempfile.TemporaryDirectory() as tmpdir:
            s = self._make_storage(tmpdir)
            s.add_entry("user", "only once")
            s.flush()
            s.flush()  # second flush is a no-op

            path = os.path.join(tmpdir, "test-session.jsonl")
            with open(path, "r") as f:
                lines = f.readlines()
            self.assertEqual(len(lines), 1)

    def test_flush_thread_safety(self):
        """Concurrent flushes should not interleave or lose data."""
        with tempfile.TemporaryDirectory() as tmpdir:
            s = self._make_storage(tmpdir)
            # Add many entries
            num_entries = 100
            for i in range(num_entries):
                s.add_entry("user", f"message-{i}")

            # Flush from multiple threads
            errors = []

            def flush_worker():
                try:
                    s.flush()
                except Exception as e:
                    errors.append(e)

            threads = [threading.Thread(target=flush_worker) for _ in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            self.assertEqual(len(errors), 0, f"Flush errors: {errors}")

            # All entries should be written exactly once
            path = os.path.join(tmpdir, "test-session.jsonl")
            with open(path, "r") as f:
                lines = [line for line in f.readlines() if line.strip()]
            self.assertEqual(len(lines), num_entries)

    def test_flush_preserves_entry_order(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            s = self._make_storage(tmpdir)
            for i in range(5):
                s.add_entry("user", f"msg-{i}")
            s.flush()

            path = os.path.join(tmpdir, "test-session.jsonl")
            with open(path, "r") as f:
                lines = f.readlines()
            for i, line in enumerate(lines):
                entry = json.loads(line)
                self.assertEqual(entry["content"], f"msg-{i}")

    # -- get_history ---------------------------------------------------------

    def test_get_history_returns_entries(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            s = self._make_storage(tmpdir)
            s.add_entry("user", "Q1")
            s.add_entry("jarvis", "A1")
            history = s.get_history()
            self.assertEqual(len(history), 2)
            self.assertEqual(history[0].content, "Q1")
            self.assertEqual(history[1].content, "A1")

    def test_get_history_limit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            s = self._make_storage(tmpdir)
            for i in range(10):
                s.add_entry("user", f"msg-{i}")
            history = s.get_history(limit=3)
            self.assertEqual(len(history), 3)
            # Should be the last 3 entries
            self.assertEqual(history[0].content, "msg-7")
            self.assertEqual(history[1].content, "msg-8")
            self.assertEqual(history[2].content, "msg-9")

    def test_get_history_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            s = self._make_storage(tmpdir)
            history = s.get_history()
            self.assertEqual(history, [])

    def test_get_history_returns_from_memory_not_disk(self):
        """get_history uses in-memory _entries, not disk, for current session."""
        with tempfile.TemporaryDirectory() as tmpdir:
            s = self._make_storage(tmpdir)
            s.add_entry("user", "in memory")
            # Do NOT flush -- entry is only in memory
            history = s.get_history()
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0].content, "in memory")

    # -- get_session_entries -------------------------------------------------

    def test_get_session_entries_current_session(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            s = self._make_storage(tmpdir)
            s.add_entry("user", "entry1")
            s.add_entry("jarvis", "entry2")
            entries = s.get_session_entries()
            self.assertEqual(len(entries), 2)

    def test_get_session_entries_other_session_from_disk(self):
        """Reading a different session should load from disk."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Write a session file for "other-session"
            other_path = os.path.join(tmpdir, "other-session.jsonl")
            entry_data = {
                "session_id": "other-session",
                "timestamp": 1700000000.0,
                "role": "user",
                "content": "from disk",
                "tool_name": "",
                "tool_args": {},
                "metadata": {},
            }
            with open(other_path, "w") as f:
                f.write(json.dumps(entry_data) + "\n")

            s = self._make_storage(tmpdir, session_id="current-session")
            entries = s.get_session_entries("other-session")
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].content, "from disk")

    # -- undo ----------------------------------------------------------------

    def test_undo_last_removes_entry(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            s = self._make_storage(tmpdir)
            s.add_entry("user", "keep")
            s.add_entry("jarvis", "remove")
            removed = s.undo_last()
            self.assertIsNotNone(removed)
            self.assertEqual(removed.content, "remove")
            self.assertEqual(len(s._entries), 1)
            self.assertEqual(s._entries[0].content, "keep")

    def test_undo_empty_returns_none(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            s = self._make_storage(tmpdir)
            result = s.undo_last()
            self.assertIsNone(result)

    # -- delete_session ------------------------------------------------------

    def test_delete_session_removes_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            s = self._make_storage(tmpdir)
            s.add_entry("user", "doomed")
            s.flush()
            path = os.path.join(tmpdir, "test-session.jsonl")
            self.assertTrue(os.path.exists(path))
            deleted = s.delete_session("test-session")
            self.assertTrue(deleted)
            self.assertFalse(os.path.exists(path))
            # In-memory entries also cleared
            self.assertEqual(len(s._entries), 0)

    def test_delete_nonexistent_session(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            s = self._make_storage(tmpdir)
            deleted = s.delete_session("no-such-session")
            self.assertFalse(deleted)

    # -- export_session ------------------------------------------------------

    def test_export_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            s = self._make_storage(tmpdir)
            s.add_entry("user", "Q")
            s.add_entry("jarvis", "A")
            exported = s.export_session(format="json")
            data = json.loads(exported)
            self.assertEqual(len(data), 2)
            self.assertEqual(data[0]["role"], "user")
            self.assertEqual(data[1]["role"], "jarvis")

    def test_export_text(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            s = self._make_storage(tmpdir)
            s.add_entry("user", "Hey")
            exported = s.export_session(format="text")
            self.assertIn("USER", exported)
            self.assertIn("Hey", exported)

    def test_export_markdown(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            s = self._make_storage(tmpdir)
            s.add_entry("user", "Question")
            exported = s.export_session(format="markdown")
            self.assertIn("# Session", exported)
            self.assertIn("USER", exported)
            self.assertIn("Question", exported)


if __name__ == "__main__":
    unittest.main()
