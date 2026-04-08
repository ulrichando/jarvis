"""Tests for src/agent/coordinator.py — AgentCoordinator base class.

Covers all pure/side-effect-free logic:
- CoordinatorMode enum values
- TaskNotification / WorkerState / AgentHandle dataclasses
- spawn_worker: ID generation, WorkerState creation, notification emission
- update_worker: state transitions, terminal-state notification, unknown ID
- get_worker / get_active_workers / get_completed_workers
- cancel_worker: not found, already terminal, running → cancelled
- should_continue_or_spawn: path overlap heuristic
- format_notification: XML structure
- get_status_summary: zero and multi-status
- drain_notifications: returns-and-clears
- cleanup: age-based pruning
- list_running / list_all / get_status / get_result / kill_agent
"""

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.agent.coordinator import (
    AgentCoordinator,
    AgentHandle,
    CoordinatorMode,
    TaskNotification,
    WorkerState,
)


# ─── CoordinatorMode enum ─────────────────────────────────────────────────────

class TestCoordinatorMode(unittest.TestCase):
    def test_direct_value(self):
        self.assertEqual(CoordinatorMode.DIRECT.value, "direct")

    def test_synthesize_value(self):
        self.assertEqual(CoordinatorMode.SYNTHESIZE.value, "synthesize")

    def test_parallel_value(self):
        self.assertEqual(CoordinatorMode.PARALLEL.value, "parallel")

    def test_three_modes(self):
        self.assertEqual(len(CoordinatorMode), 3)


# ─── TaskNotification dataclass ───────────────────────────────────────────────

class TestTaskNotification(unittest.TestCase):
    def test_required_fields(self):
        n = TaskNotification(
            task_id="abc123",
            agent_type="worker",
            status="running",
            summary="doing stuff",
            result="partial",
        )
        self.assertEqual(n.task_id, "abc123")
        self.assertEqual(n.agent_type, "worker")
        self.assertEqual(n.status, "running")
        self.assertEqual(n.summary, "doing stuff")
        self.assertEqual(n.result, "partial")

    def test_defaults(self):
        n = TaskNotification(
            task_id="x", agent_type="scout",
            status="pending", summary="s", result="",
        )
        self.assertEqual(n.token_usage, 0)
        self.assertEqual(n.duration_ms, 0)


# ─── WorkerState dataclass ────────────────────────────────────────────────────

class TestWorkerState(unittest.TestCase):
    def test_fields(self):
        ws = WorkerState(
            agent_id="id1", agent_type="worker",
            task="do something", status="pending",
        )
        self.assertEqual(ws.agent_id, "id1")
        self.assertFalse(ws.is_backgrounded)
        self.assertIsNone(ws.result)
        self.assertIsNone(ws.completed_at)

    def test_started_at_auto(self):
        before = time.time()
        ws = WorkerState(agent_id="x", agent_type="scout", task="t", status="pending")
        after = time.time()
        self.assertGreaterEqual(ws.started_at, before)
        self.assertLessEqual(ws.started_at, after)


# ─── AgentHandle dataclass ────────────────────────────────────────────────────

class TestAgentHandle(unittest.TestCase):
    def test_defaults(self):
        h = AgentHandle(id="h1", agent_type="planner", task="plan it")
        self.assertEqual(h.status, "pending")
        self.assertEqual(h.result, "")
        self.assertEqual(h.error, "")
        self.assertIsNone(h._thread)

    def test_created_at_auto(self):
        before = time.time()
        h = AgentHandle(id="h2", agent_type="worker", task="work")
        after = time.time()
        self.assertGreaterEqual(h.created_at, before)
        self.assertLessEqual(h.created_at, after)


# ─── AgentCoordinator.spawn_worker ────────────────────────────────────────────

class TestSpawnWorker(unittest.TestCase):
    def setUp(self):
        self.c = AgentCoordinator()

    def test_returns_12_char_hex_id(self):
        aid = self.c.spawn_worker("worker", "do stuff")
        self.assertEqual(len(aid), 12)
        int(aid, 16)  # must be valid hex

    def test_worker_state_created(self):
        aid = self.c.spawn_worker("scout", "investigate")
        ws = self.c.get_worker(aid)
        self.assertIsNotNone(ws)
        self.assertEqual(ws.agent_type, "scout")
        self.assertEqual(ws.task, "investigate")
        self.assertEqual(ws.status, "pending")

    def test_background_flag_stored(self):
        aid = self.c.spawn_worker("worker", "t", background=True)
        ws = self.c.get_worker(aid)
        self.assertTrue(ws.is_backgrounded)

    def test_notification_emitted_on_spawn(self):
        self.c.spawn_worker("worker", "task")
        notifs = self.c.drain_notifications()
        self.assertEqual(len(notifs), 1)
        self.assertIn("worker", notifs[0].agent_type)

    def test_unique_ids(self):
        ids = {self.c.spawn_worker("worker", "t") for _ in range(20)}
        self.assertEqual(len(ids), 20)


# ─── AgentCoordinator.update_worker ──────────────────────────────────────────

class TestUpdateWorker(unittest.TestCase):
    def setUp(self):
        self.c = AgentCoordinator()

    def test_status_updated(self):
        aid = self.c.spawn_worker("worker", "t")
        self.c.drain_notifications()  # clear spawn notif
        self.c.update_worker(aid, "running")
        ws = self.c.get_worker(aid)
        self.assertEqual(ws.status, "running")

    def test_result_stored(self):
        aid = self.c.spawn_worker("worker", "t")
        self.c.update_worker(aid, "completed", result="done output")
        ws = self.c.get_worker(aid)
        self.assertEqual(ws.result, "done output")

    def test_terminal_status_emits_notification(self):
        aid = self.c.spawn_worker("worker", "t")
        self.c.drain_notifications()
        self.c.update_worker(aid, "completed")
        notifs = self.c.drain_notifications()
        self.assertEqual(len(notifs), 1)
        self.assertEqual(notifs[0].status, "completed")

    def test_completed_at_set_on_terminal(self):
        aid = self.c.spawn_worker("worker", "t")
        self.c.update_worker(aid, "failed")
        ws = self.c.get_worker(aid)
        self.assertIsNotNone(ws.completed_at)

    def test_non_terminal_status_no_notification(self):
        aid = self.c.spawn_worker("worker", "t")
        self.c.drain_notifications()
        self.c.update_worker(aid, "running")
        notifs = self.c.drain_notifications()
        self.assertEqual(len(notifs), 0)

    def test_unknown_id_no_crash(self):
        self.c.update_worker("deadbeef", "completed")  # should not raise

    def test_all_terminal_statuses_emit(self):
        for status in ("completed", "failed", "cancelled"):
            c = AgentCoordinator()
            aid = c.spawn_worker("worker", "t")
            c.drain_notifications()
            c.update_worker(aid, status)
            notifs = c.drain_notifications()
            self.assertEqual(len(notifs), 1, f"No notification for {status}")


# ─── get_worker / get_active / get_completed ──────────────────────────────────

class TestWorkerQueries(unittest.TestCase):
    def setUp(self):
        self.c = AgentCoordinator()

    def test_get_worker_none_for_unknown(self):
        self.assertIsNone(self.c.get_worker("unknown"))

    def test_get_active_workers_only_pending_running(self):
        aid1 = self.c.spawn_worker("worker", "t1")  # pending
        aid2 = self.c.spawn_worker("worker", "t2")
        self.c.update_worker(aid2, "running")
        aid3 = self.c.spawn_worker("worker", "t3")
        self.c.update_worker(aid3, "completed")

        active = self.c.get_active_workers()
        active_ids = {w.agent_id for w in active}
        self.assertIn(aid1, active_ids)
        self.assertIn(aid2, active_ids)
        self.assertNotIn(aid3, active_ids)

    def test_get_completed_workers_only_terminal(self):
        aid1 = self.c.spawn_worker("worker", "t1")
        self.c.update_worker(aid1, "completed")
        aid2 = self.c.spawn_worker("worker", "t2")
        self.c.update_worker(aid2, "failed")
        aid3 = self.c.spawn_worker("worker", "t3")  # still pending

        done = self.c.get_completed_workers()
        done_ids = {w.agent_id for w in done}
        self.assertIn(aid1, done_ids)
        self.assertIn(aid2, done_ids)
        self.assertNotIn(aid3, done_ids)

    def test_cancelled_in_completed(self):
        aid = self.c.spawn_worker("worker", "t")
        self.c.update_worker(aid, "cancelled")
        done = self.c.get_completed_workers()
        self.assertEqual(len(done), 1)


# ─── cancel_worker ────────────────────────────────────────────────────────────

class TestCancelWorker(unittest.TestCase):
    def setUp(self):
        self.c = AgentCoordinator()

    def test_not_found_returns_false(self):
        self.assertFalse(self.c.cancel_worker("nosuchid"))

    def test_already_completed_returns_false(self):
        aid = self.c.spawn_worker("worker", "t")
        self.c.update_worker(aid, "completed")
        self.assertFalse(self.c.cancel_worker(aid))

    def test_already_failed_returns_false(self):
        aid = self.c.spawn_worker("worker", "t")
        self.c.update_worker(aid, "failed")
        self.assertFalse(self.c.cancel_worker(aid))

    def test_pending_becomes_cancelled(self):
        aid = self.c.spawn_worker("worker", "t")
        result = self.c.cancel_worker(aid)
        self.assertTrue(result)
        ws = self.c.get_worker(aid)
        self.assertEqual(ws.status, "cancelled")


# ─── should_continue_or_spawn ────────────────────────────────────────────────

class TestShouldContinueOrSpawn(unittest.TestCase):
    def setUp(self):
        self.c = AgentCoordinator()

    def test_unknown_id_returns_spawn(self):
        self.assertEqual(self.c.should_continue_or_spawn("noexist", "any task"), "spawn")

    def test_pending_status_returns_spawn(self):
        aid = self.c.spawn_worker("worker", "task about src/foo.py")
        result = self.c.should_continue_or_spawn(aid, "task about src/foo.py")
        # pending is not running/completed → spawn
        self.assertEqual(result, "spawn")

    def test_running_high_overlap_continues(self):
        aid = self.c.spawn_worker("worker", "fix bug in src/foo.py src/bar.py")
        self.c.update_worker(aid, "running")
        # new task references same files
        result = self.c.should_continue_or_spawn(aid, "update tests in src/foo.py src/bar.py")
        self.assertEqual(result, "continue")

    def test_running_low_overlap_spawns(self):
        aid = self.c.spawn_worker("worker", "fix src/foo.py")
        self.c.update_worker(aid, "running")
        # new task references completely different files
        result = self.c.should_continue_or_spawn(aid, "update src/zzz.py src/yyy.py src/xxx.py")
        self.assertEqual(result, "spawn")

    def test_no_paths_in_new_task_returns_spawn(self):
        aid = self.c.spawn_worker("worker", "fix src/foo.py")
        self.c.update_worker(aid, "running")
        result = self.c.should_continue_or_spawn(aid, "explain how async works")
        self.assertEqual(result, "spawn")


# ─── format_notification ─────────────────────────────────────────────────────

class TestFormatNotification(unittest.TestCase):
    def setUp(self):
        self.c = AgentCoordinator()

    def test_xml_structure(self):
        aid = self.c.spawn_worker("scout", "investigate something")
        self.c.update_worker(aid, "completed", result="found stuff")
        ws = self.c.get_worker(aid)
        output = self.c.format_notification(ws)
        self.assertIn("<task-notification>", output)
        self.assertIn("<task-id>", output)
        self.assertIn("<status>", output)
        self.assertIn("<summary>", output)
        self.assertIn("<result>", output)
        self.assertIn("</task-notification>", output)

    def test_duration_included_when_completed(self):
        aid = self.c.spawn_worker("worker", "t")
        self.c.update_worker(aid, "completed")
        ws = self.c.get_worker(aid)
        output = self.c.format_notification(ws)
        self.assertIn("duration-ms", output)

    def test_result_truncated_at_2000(self):
        aid = self.c.spawn_worker("worker", "t")
        self.c.update_worker(aid, "completed", result="X" * 5000)
        ws = self.c.get_worker(aid)
        output = self.c.format_notification(ws)
        # Result section should not contain 5000 X's
        self.assertLess(output.count("X"), 2100)

    def test_task_id_in_output(self):
        aid = self.c.spawn_worker("worker", "task")
        ws = self.c.get_worker(aid)
        output = self.c.format_notification(ws)
        self.assertIn(aid, output)


# ─── get_status_summary ───────────────────────────────────────────────────────

class TestGetStatusSummary(unittest.TestCase):
    def test_zero_workers(self):
        c = AgentCoordinator()
        self.assertEqual(c.get_status_summary(), "0 workers")

    def test_counts_by_status(self):
        c = AgentCoordinator()
        c.spawn_worker("worker", "t1")
        c.spawn_worker("worker", "t2")
        aid = c.spawn_worker("worker", "t3")
        c.update_worker(aid, "completed")
        summary = c.get_status_summary()
        self.assertIn("3 workers", summary)
        self.assertIn("pending", summary)
        self.assertIn("completed", summary)


# ─── drain_notifications ─────────────────────────────────────────────────────

class TestDrainNotifications(unittest.TestCase):
    def test_empty_returns_empty_list(self):
        c = AgentCoordinator()
        self.assertEqual(c.drain_notifications(), [])

    def test_draining_clears_list(self):
        c = AgentCoordinator()
        c.spawn_worker("worker", "t")
        first = c.drain_notifications()
        second = c.drain_notifications()
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 0)

    def test_returns_all_pending_notifications(self):
        c = AgentCoordinator()
        for _ in range(3):
            c.spawn_worker("worker", "task")
        notifs = c.drain_notifications()
        self.assertEqual(len(notifs), 3)


# ─── cleanup ──────────────────────────────────────────────────────────────────

class TestCleanup(unittest.TestCase):
    def test_removes_old_done_handles(self):
        c = AgentCoordinator()
        # Manually add an old done handle
        h = AgentHandle(id="old1", agent_type="worker", task="old task", status="done")
        h.created_at = time.time() - 7200  # 2 hours ago
        c._agents["old1"] = h
        c.cleanup(max_age=3600)
        self.assertNotIn("old1", c._agents)

    def test_keeps_fresh_handles(self):
        c = AgentCoordinator()
        h = AgentHandle(id="new1", agent_type="worker", task="new task", status="done")
        h.created_at = time.time() - 10  # 10 seconds ago
        c._agents["new1"] = h
        c.cleanup(max_age=3600)
        self.assertIn("new1", c._agents)

    def test_keeps_running_handles_regardless_of_age(self):
        c = AgentCoordinator()
        h = AgentHandle(id="run1", agent_type="worker", task="running task", status="running")
        h.created_at = time.time() - 7200
        c._agents["run1"] = h
        c.cleanup(max_age=3600)
        self.assertIn("run1", c._agents)


# ─── list_running / list_all / get_status / get_result / kill_agent ───────────

class TestAgentManagement(unittest.TestCase):
    def setUp(self):
        self.c = AgentCoordinator()
        self.h1 = AgentHandle(id="h1", agent_type="scout", task="scout task", status="running")
        self.h2 = AgentHandle(id="h2", agent_type="worker", task="work task", status="done")
        self.h2.result = "work done"
        self.c._agents["h1"] = self.h1
        self.c._agents["h2"] = self.h2

    def test_list_running_only_active(self):
        running = self.c.list_running()
        ids = [r["id"] for r in running]
        self.assertIn("h1", ids)
        self.assertNotIn("h2", ids)

    def test_list_all_includes_all(self):
        all_handles = self.c.list_all()
        ids = [r["id"] for r in all_handles]
        self.assertIn("h1", ids)
        self.assertIn("h2", ids)

    def test_get_status_unknown_returns_none(self):
        self.assertIsNone(self.c.get_status("nosuch"))

    def test_get_status_known_returns_dict(self):
        status = self.c.get_status("h1")
        self.assertIsNotNone(status)
        self.assertEqual(status["id"], "h1")
        self.assertEqual(status["type"], "scout")
        self.assertIn("status", status)
        self.assertIn("age", status)

    def test_get_result_unknown_returns_empty(self):
        self.assertEqual(self.c.get_result("nosuch"), "")

    def test_get_result_known(self):
        self.assertEqual(self.c.get_result("h2"), "work done")

    def test_kill_agent_unknown_returns_false(self):
        self.assertFalse(self.c.kill_agent("nosuch"))

    def test_kill_agent_sets_cancelled(self):
        result = self.c.kill_agent("h1")
        self.assertTrue(result)
        self.assertEqual(self.c._agents["h1"].status, "cancelled")


if __name__ == "__main__":
    unittest.main()
