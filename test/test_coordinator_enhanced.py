"""Tests for src/agent/coordinator_enhanced.py — CoordinatorAgent, TaskGraph, etc.

Covers all pure/near-pure logic that does NOT require a live LLM or threads:
- SubTask dataclass + duration_ms property
- TaskGraph: add_task, get_ready_tasks, is_complete, has_failures, get_results, summary
- TaskGraph.get_ready_tasks respects dependencies and priority ordering
- ProgressTracker: emit fires callbacks, exception-safe, get_updates, clear
- CoordinatorAgent.decompose without reasoner → single fallback task graph
- CoordinatorAgent.assign_agent: exact match and overlap scoring
- CoordinatorAgent._build_task_context: structure, dependency inclusion
- CoordinatorAgent.fan_in without reasoner: concatenation fallback
- CoordinatorAgent.get_coordinator_prompt: required sections, MCP servers
- CoordinatorAgent.get_status: dict keys, no-active-graph sentinel
- CoordinatorAgent._poll_running_tasks: completed / retry / exhausted / cancelled
"""

import asyncio
import os
import sys
import time
import unittest
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.agent.coordinator_enhanced import (
    AgentCapability,
    CoordinatorAgent,
    ProgressTracker,
    ProgressUpdate,
    SubTask,
    TaskGraph,
    TaskStatus,
)
from src.agent.coordinator import AgentCoordinator


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ─── SubTask dataclass ────────────────────────────────────────────────────────

class TestSubTask(unittest.TestCase):
    def test_defaults(self):
        t = SubTask()
        self.assertEqual(t.description, "")
        self.assertEqual(t.agent_type, "worker")
        self.assertEqual(t.status, TaskStatus.PENDING)
        self.assertEqual(t.dependencies, [])
        self.assertEqual(t.retries, 0)
        self.assertEqual(t.max_retries, 2)
        self.assertEqual(t.priority, 0)

    def test_id_auto_generated(self):
        t1, t2 = SubTask(), SubTask()
        self.assertNotEqual(t1.id, t2.id)
        self.assertEqual(len(t1.id), 8)

    def test_duration_ms_zero_without_times(self):
        t = SubTask()
        self.assertEqual(t.duration_ms, 0)

    def test_duration_ms_with_times(self):
        t = SubTask()
        t.started_at = 1000.0
        t.completed_at = 1002.5
        self.assertEqual(t.duration_ms, 2500)

    def test_custom_capabilities(self):
        t = SubTask(required_capabilities={AgentCapability.READ_ONLY})
        self.assertIn(AgentCapability.READ_ONLY, t.required_capabilities)


# ─── TaskGraph ────────────────────────────────────────────────────────────────

class TestTaskGraph(unittest.TestCase):
    def _make_graph(self):
        g = TaskGraph(goal="test goal")
        return g

    def test_add_task(self):
        g = self._make_graph()
        t = SubTask(description="step one")
        g.add_task(t)
        self.assertIn(t.id, g.tasks)

    def test_get_ready_no_deps(self):
        g = self._make_graph()
        t1 = SubTask(description="A", dependencies=[])
        t2 = SubTask(description="B", dependencies=[])
        g.add_task(t1)
        g.add_task(t2)
        ready = g.get_ready_tasks()
        self.assertEqual(len(ready), 2)

    def test_get_ready_unmet_deps_excluded(self):
        g = self._make_graph()
        t1 = SubTask(description="A")
        t2 = SubTask(description="B", dependencies=[t1.id])
        g.add_task(t1)
        g.add_task(t2)
        # t1 still pending → t2 not ready
        ready = g.get_ready_tasks()
        ids = [t.id for t in ready]
        self.assertIn(t1.id, ids)
        self.assertNotIn(t2.id, ids)

    def test_get_ready_met_deps_included(self):
        g = self._make_graph()
        t1 = SubTask(description="A")
        t1.status = TaskStatus.COMPLETED
        t2 = SubTask(description="B", dependencies=[t1.id])
        g.add_task(t1)
        g.add_task(t2)
        ready = g.get_ready_tasks()
        ids = [t.id for t in ready]
        self.assertIn(t2.id, ids)

    def test_get_ready_sorted_by_priority_desc(self):
        g = self._make_graph()
        low = SubTask(description="low priority", priority=1)
        high = SubTask(description="high priority", priority=10)
        g.add_task(low)
        g.add_task(high)
        ready = g.get_ready_tasks()
        self.assertEqual(ready[0].priority, 10)

    def test_get_ready_skips_non_pending(self):
        g = self._make_graph()
        t = SubTask(description="running task")
        t.status = TaskStatus.RUNNING
        g.add_task(t)
        ready = g.get_ready_tasks()
        self.assertEqual(len(ready), 0)

    def test_is_complete_all_terminal(self):
        g = self._make_graph()
        t1 = SubTask(description="A")
        t1.status = TaskStatus.COMPLETED
        t2 = SubTask(description="B")
        t2.status = TaskStatus.FAILED
        g.add_task(t1)
        g.add_task(t2)
        self.assertTrue(g.is_complete())

    def test_is_complete_false_when_pending(self):
        g = self._make_graph()
        t = SubTask(description="A")
        g.add_task(t)
        self.assertFalse(g.is_complete())

    def test_is_complete_skipped_is_terminal(self):
        g = self._make_graph()
        t = SubTask(description="A")
        t.status = TaskStatus.SKIPPED
        g.add_task(t)
        self.assertTrue(g.is_complete())

    def test_has_failures_true(self):
        g = self._make_graph()
        t = SubTask(description="bad")
        t.status = TaskStatus.FAILED
        g.add_task(t)
        self.assertTrue(g.has_failures())

    def test_has_failures_false(self):
        g = self._make_graph()
        t = SubTask(description="good")
        t.status = TaskStatus.COMPLETED
        g.add_task(t)
        self.assertFalse(g.has_failures())

    def test_get_results_only_completed_with_result(self):
        g = self._make_graph()
        t1 = SubTask(description="A")
        t1.status = TaskStatus.COMPLETED
        t1.result = "output A"
        t2 = SubTask(description="B")
        t2.status = TaskStatus.FAILED
        t2.result = "error B"
        t3 = SubTask(description="C")
        t3.status = TaskStatus.COMPLETED
        t3.result = ""  # completed but no result
        g.add_task(t1)
        g.add_task(t2)
        g.add_task(t3)
        results = g.get_results()
        self.assertIn(t1.id, results)
        self.assertNotIn(t2.id, results)
        self.assertNotIn(t3.id, results)

    def test_summary_format(self):
        g = self._make_graph()
        t1 = SubTask()
        t1.status = TaskStatus.COMPLETED
        t2 = SubTask()
        t2.status = TaskStatus.PENDING
        g.add_task(t1)
        g.add_task(t2)
        summary = g.summary()
        self.assertIn("2 tasks", summary)
        self.assertIn("completed", summary)
        self.assertIn("pending", summary)

    def test_empty_graph_is_complete(self):
        g = self._make_graph()
        self.assertTrue(g.is_complete())

    def test_dependency_on_unknown_id_treated_as_met(self):
        """Deps on IDs not in the graph are ignored (treated as met)."""
        g = self._make_graph()
        t = SubTask(description="B", dependencies=["nonexistent_id"])
        g.add_task(t)
        ready = g.get_ready_tasks()
        self.assertIn(t.id, [r.id for r in ready])


# ─── ProgressTracker ──────────────────────────────────────────────────────────

class TestProgressTracker(unittest.TestCase):
    def test_emit_fires_callbacks(self):
        pt = ProgressTracker()
        received = []
        pt.on_progress(lambda u: received.append(u))
        pt.emit("t1", "do stuff", "running", "started")
        self.assertEqual(len(received), 1)
        self.assertIsInstance(received[0], ProgressUpdate)

    def test_emit_fires_all_callbacks(self):
        pt = ProgressTracker()
        a, b = [], []
        pt.on_progress(lambda u: a.append(u))
        pt.on_progress(lambda u: b.append(u))
        pt.emit("t1", "task", "done", "completed")
        self.assertEqual(len(a), 1)
        self.assertEqual(len(b), 1)

    def test_callback_exception_doesnt_propagate(self):
        pt = ProgressTracker()
        pt.on_progress(lambda u: (_ for _ in ()).throw(RuntimeError("boom")))
        # Should not raise
        pt.emit("t1", "task", "done", "msg")

    def test_get_updates_returns_history(self):
        pt = ProgressTracker()
        pt.emit("t1", "task", "running", "msg1")
        pt.emit("t2", "task2", "done", "msg2")
        updates = pt.get_updates()
        self.assertEqual(len(updates), 2)

    def test_clear_empties_history(self):
        pt = ProgressTracker()
        pt.emit("t1", "task", "done", "msg")
        pt.clear()
        self.assertEqual(len(pt.get_updates()), 0)

    def test_update_fields(self):
        pt = ProgressTracker()
        pt.emit("t1", "my task desc", "completed", "all done")
        u = pt.get_updates()[0]
        self.assertEqual(u.task_id, "t1")
        self.assertEqual(u.task_desc, "my task desc")
        self.assertEqual(u.status, "completed")
        self.assertEqual(u.message, "all done")


# ─── CoordinatorAgent.decompose (no reasoner) ─────────────────────────────────

class TestCoordinatorAgentDecompose(unittest.TestCase):
    def test_no_reasoner_returns_single_task(self):
        ca = CoordinatorAgent()
        graph = run(ca.decompose("do something"))
        self.assertEqual(len(graph.tasks), 1)
        task = list(graph.tasks.values())[0]
        self.assertEqual(task.description, "do something")

    def test_no_reasoner_goal_stored(self):
        ca = CoordinatorAgent()
        graph = run(ca.decompose("build a feature"))
        self.assertEqual(graph.goal, "build a feature")

    def test_reasoner_json_error_falls_back(self):
        ca = CoordinatorAgent()
        r = MagicMock()
        r.query = AsyncMock(return_value=("not valid json", "mock"))
        ca.set_reasoner(r)
        graph = run(ca.decompose("some task"))
        # Falls back to single task
        self.assertEqual(len(graph.tasks), 1)


# ─── CoordinatorAgent.assign_agent ───────────────────────────────────────────

class TestAssignAgent(unittest.TestCase):
    def setUp(self):
        self.ca = CoordinatorAgent()

    def test_scout_exact_match(self):
        from src.agent.coordinator_enhanced import AGENT_CAPABILITIES
        t = SubTask(required_capabilities=AGENT_CAPABILITIES["scout"])
        agent = self.ca.assign_agent(t)
        self.assertEqual(agent, "scout")

    def test_full_capability_returns_worker(self):
        t = SubTask(required_capabilities={AgentCapability.FULL})
        agent = self.ca.assign_agent(t)
        self.assertEqual(agent, "worker")

    def test_planner_exact_match(self):
        from src.agent.coordinator_enhanced import AGENT_CAPABILITIES
        t = SubTask(required_capabilities=AGENT_CAPABILITIES["planner"])
        agent = self.ca.assign_agent(t)
        self.assertEqual(agent, "planner")

    def test_unknown_capabilities_uses_task_suggestion(self):
        t = SubTask(agent_type="worker", required_capabilities=set())
        agent = self.ca.assign_agent(t)
        self.assertIsInstance(agent, str)
        self.assertGreater(len(agent), 0)


# ─── CoordinatorAgent._build_task_context ────────────────────────────────────

class TestBuildTaskContext(unittest.TestCase):
    def setUp(self):
        self.ca = CoordinatorAgent()

    def test_includes_goal_and_task(self):
        g = TaskGraph(goal="my goal")
        t = SubTask(description="do something specific")
        g.add_task(t)
        ctx = self.ca._build_task_context(t, g)
        self.assertIn("my goal", ctx)
        self.assertIn("do something specific", ctx)

    def test_no_deps_no_dep_section(self):
        g = TaskGraph(goal="goal")
        t = SubTask(description="independent task")
        g.add_task(t)
        ctx = self.ca._build_task_context(t, g)
        self.assertNotIn("prerequisite", ctx)

    def test_completed_dep_result_included(self):
        g = TaskGraph(goal="goal")
        t1 = SubTask(description="research phase")
        t1.status = TaskStatus.COMPLETED
        t1.result = "research findings here"
        g.add_task(t1)
        t2 = SubTask(description="implement phase", dependencies=[t1.id])
        g.add_task(t2)
        ctx = self.ca._build_task_context(t2, g)
        self.assertIn("research findings here", ctx)
        self.assertIn("prerequisite", ctx.lower())

    def test_non_completed_dep_excluded(self):
        g = TaskGraph(goal="goal")
        t1 = SubTask(description="pending dep")
        t1.status = TaskStatus.PENDING
        t1.result = "this should not appear"
        g.add_task(t1)
        t2 = SubTask(description="task", dependencies=[t1.id])
        g.add_task(t2)
        ctx = self.ca._build_task_context(t2, g)
        self.assertNotIn("this should not appear", ctx)

    def test_extra_context_included(self):
        g = TaskGraph(goal="goal")
        t = SubTask(description="task", context="extra info here")
        g.add_task(t)
        ctx = self.ca._build_task_context(t, g)
        self.assertIn("extra info here", ctx)


# ─── CoordinatorAgent.fan_in (no reasoner) ───────────────────────────────────

class TestFanIn(unittest.TestCase):
    def test_no_reasoner_concatenates(self):
        ca = CoordinatorAgent()
        results = [
            {"task": "task A", "status": "done", "result": "output A"},
            {"task": "task B", "status": "done", "result": "output B"},
        ]
        output = run(ca.fan_in("my goal", results))
        self.assertIn("my goal", output)
        self.assertIn("output A", output)
        self.assertIn("output B", output)

    def test_no_reasoner_includes_task_names(self):
        ca = CoordinatorAgent()
        results = [{"task": "research step", "status": "done", "result": "findings"}]
        output = run(ca.fan_in("goal", results))
        self.assertIn("research step", output)

    def test_reasoner_exception_falls_back(self):
        ca = CoordinatorAgent()
        r = MagicMock()
        r.query = AsyncMock(side_effect=RuntimeError("LLM fail"))
        ca.set_reasoner(r)
        results = [{"task": "t", "status": "done", "result": "res"}]
        output = run(ca.fan_in("goal", results))
        # Falls back to concatenation
        self.assertIsInstance(output, str)
        self.assertIn("res", output)

    def test_empty_results(self):
        ca = CoordinatorAgent()
        output = run(ca.fan_in("goal", []))
        self.assertIn("goal", output)


# ─── CoordinatorAgent.get_coordinator_prompt ─────────────────────────────────

class TestGetCoordinatorPrompt(unittest.TestCase):
    def setUp(self):
        self.ca = CoordinatorAgent()

    def test_contains_coordinator_mode(self):
        prompt = self.ca.get_coordinator_prompt()
        self.assertIn("coordinator mode", prompt.lower())

    def test_contains_dispatch(self):
        prompt = self.ca.get_coordinator_prompt()
        self.assertIn("dispatch", prompt)

    def test_contains_worker_tools(self):
        prompt = self.ca.get_coordinator_prompt()
        self.assertIn("read_file", prompt)

    def test_contains_workflow_sections(self):
        prompt = self.ca.get_coordinator_prompt()
        self.assertIn("Research", prompt)
        self.assertIn("Synthesis", prompt)
        self.assertIn("Implementation", prompt)

    def test_mcp_servers_appended(self):
        prompt = self.ca.get_coordinator_prompt(mcp_servers=["filesystem", "github"])
        self.assertIn("filesystem", prompt)
        self.assertIn("github", prompt)

    def test_no_mcp_no_mcp_line(self):
        prompt = self.ca.get_coordinator_prompt(mcp_servers=None)
        self.assertNotIn("MCP tools from", prompt)


# ─── CoordinatorAgent.get_status ──────────────────────────────────────────────

class TestGetStatus(unittest.TestCase):
    def test_returns_dict_with_required_keys(self):
        ca = CoordinatorAgent()
        status = ca.get_status()
        self.assertIn("base", status)
        self.assertIn("graph", status)
        self.assertIn("progress_updates", status)

    def test_no_active_graph_sentinel(self):
        ca = CoordinatorAgent()
        status = ca.get_status()
        self.assertEqual(status["graph"], "No active graph")

    def test_progress_updates_count(self):
        ca = CoordinatorAgent()
        ca._progress.emit("t", "task", "done", "msg")
        ca._progress.emit("t2", "task2", "done", "msg2")
        status = ca.get_status()
        self.assertEqual(status["progress_updates"], 2)


# ─── CoordinatorAgent._poll_running_tasks ─────────────────────────────────────

class TestPollRunningTasks(unittest.TestCase):
    def _make_ca_with_graph(self):
        base = AgentCoordinator()
        ca = CoordinatorAgent(base_coordinator=base)
        g = TaskGraph(goal="test")
        return ca, base, g

    def test_completed_worker_marks_task_completed(self):
        ca, base, g = self._make_ca_with_graph()
        t = SubTask(description="task")
        t.status = TaskStatus.RUNNING
        g.add_task(t)
        # Inject completed worker
        aid = base.spawn_worker("worker", "task")
        base.update_worker(aid, "completed", result="done output")
        t.agent_id = aid
        ca._poll_running_tasks(g)
        self.assertEqual(t.status, TaskStatus.COMPLETED)
        self.assertEqual(t.result, "done output")

    def test_failed_worker_with_retries_resets_to_pending(self):
        ca, base, g = self._make_ca_with_graph()
        t = SubTask(description="task", max_retries=2)
        t.status = TaskStatus.RUNNING
        t.retries = 0
        g.add_task(t)
        aid = base.spawn_worker("worker", "task")
        base.update_worker(aid, "failed", result="error msg")
        t.agent_id = aid
        ca._poll_running_tasks(g)
        self.assertEqual(t.status, TaskStatus.PENDING)
        self.assertEqual(t.retries, 1)
        self.assertEqual(t.agent_id, "")

    def test_failed_worker_exhausted_retries_marks_failed(self):
        ca, base, g = self._make_ca_with_graph()
        t = SubTask(description="task", max_retries=2)
        t.status = TaskStatus.RUNNING
        t.retries = 2  # already at max
        g.add_task(t)
        aid = base.spawn_worker("worker", "task")
        base.update_worker(aid, "failed", result="final error")
        t.agent_id = aid
        ca._poll_running_tasks(g)
        self.assertEqual(t.status, TaskStatus.FAILED)

    def test_cancelled_worker_marks_task_failed(self):
        ca, base, g = self._make_ca_with_graph()
        t = SubTask(description="task")
        t.status = TaskStatus.RUNNING
        g.add_task(t)
        aid = base.spawn_worker("worker", "task")
        base.update_worker(aid, "cancelled")
        t.agent_id = aid
        ca._poll_running_tasks(g)
        self.assertEqual(t.status, TaskStatus.FAILED)
        self.assertEqual(t.error, "Cancelled")

    def test_skips_non_running_tasks(self):
        ca, base, g = self._make_ca_with_graph()
        t = SubTask(description="task")
        t.status = TaskStatus.PENDING  # not RUNNING
        g.add_task(t)
        ca._poll_running_tasks(g)
        self.assertEqual(t.status, TaskStatus.PENDING)

    def test_skips_task_without_agent_id(self):
        ca, base, g = self._make_ca_with_graph()
        t = SubTask(description="task")
        t.status = TaskStatus.RUNNING
        t.agent_id = ""
        g.add_task(t)
        ca._poll_running_tasks(g)
        self.assertEqual(t.status, TaskStatus.RUNNING)


if __name__ == "__main__":
    unittest.main()
