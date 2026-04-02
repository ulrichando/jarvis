"""Tests for brain/tasks/manager.py — SQLite-backed task tracking."""

import os
import sys
import shutil
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Ensure data dir exists for ensure_dirs()
_tmpdir = tempfile.mkdtemp(prefix="jarvis_test_tasks_")
os.environ["JARVIS_HOME"] = _tmpdir

import importlib
import brain.config
importlib.reload(brain.config)

from brain.tasks.manager import TaskManager, VALID_STATUSES, VALID_PRIORITIES


class TestTaskManager(unittest.TestCase):
    """TaskManager CRUD tests."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="jarvis_tasks_test_")
        os.environ["JARVIS_HOME"] = self.tmpdir
        importlib.reload(brain.config)
        self.db_path = Path(self.tmpdir) / "tasks.db"
        self.tm = TaskManager(db_path=self.db_path)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_create_task(self):
        task = self.tm.create("Build test suite", priority="high")
        self.assertEqual(task.title, "Build test suite")
        self.assertEqual(task.status, "pending")
        self.assertEqual(task.priority, "high")
        self.assertTrue(len(task.id) > 0)

    def test_update_status(self):
        task = self.tm.create("Status test")
        updated = self.tm.update_status(task.id, "in_progress")
        self.assertIsNotNone(updated)
        self.assertEqual(updated.status, "in_progress")

        # Invalid status should raise
        with self.assertRaises(ValueError):
            self.tm.update_status(task.id, "invalid_status")

    def test_list_tasks_all(self):
        self.tm.create("Task A")
        self.tm.create("Task B")
        self.tm.create("Task C")
        tasks = self.tm.list_tasks()
        self.assertEqual(len(tasks), 3)

    def test_list_tasks_filtered(self):
        t1 = self.tm.create("Pending task")
        t2 = self.tm.create("Done task")
        self.tm.update_status(t2.id, "done")

        pending = self.tm.list_tasks(status_filter="pending")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].title, "Pending task")

        done = self.tm.list_tasks(status_filter="done")
        self.assertEqual(len(done), 1)
        self.assertEqual(done[0].title, "Done task")

    def test_get_task(self):
        task = self.tm.create("Get me")
        fetched = self.tm.get(task.id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.title, "Get me")

        # Non-existent
        self.assertIsNone(self.tm.get("nonexistent_id"))

    def test_delete_task(self):
        task = self.tm.create("Delete me")
        self.assertTrue(self.tm.delete(task.id))
        self.assertIsNone(self.tm.get(task.id))
        # Deleting again should return False
        self.assertFalse(self.tm.delete(task.id))

    def test_task_priorities(self):
        """Tasks should be listed in priority order (critical > high > medium > low)."""
        self.tm.create("Low task", priority="low")
        self.tm.create("Critical task", priority="critical")
        self.tm.create("High task", priority="high")
        self.tm.create("Medium task", priority="medium")

        tasks = self.tm.list_tasks()
        priorities = [t.priority for t in tasks]
        self.assertEqual(priorities[0], "critical")
        self.assertEqual(priorities[1], "high")
        self.assertEqual(priorities[2], "medium")
        self.assertEqual(priorities[3], "low")

    def test_invalid_priority_rejected(self):
        with self.assertRaises(ValueError):
            self.tm.create("Bad priority", priority="urgent")

    def test_count(self):
        self.tm.create("A")
        self.tm.create("B")
        t = self.tm.create("C")
        self.tm.update_status(t.id, "done")
        self.assertEqual(self.tm.count(), 3)
        self.assertEqual(self.tm.count(status_filter="done"), 1)
        self.assertEqual(self.tm.count(status_filter="pending"), 2)


if __name__ == "__main__":
    unittest.main()
