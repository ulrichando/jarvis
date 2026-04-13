"""Tests for src/agent/swarm.py — Swarm, SwarmAgent, SubtaskResult, specialists.

Covers all pure/near-pure logic that does NOT require a live LLM:
- Dataclass fields and defaults
- SPECIALIST_AGENTS registry completeness
- _decompose JSON parsing, validation, capping, unknown-agent defaulting
- _run_single result unwrapping
- _aggregate fallback path (exceptions, partial failures, single result)
- on_progress callback firing
- set_reasoner / reasoner property
"""

import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.agent.swarm import (
    SPECIALIST_AGENTS,
    Swarm,
    SwarmAgent,
    SubtaskResult,
)


# ─── helpers ──────────────────────────────────────────────────────────────────

def run(coro):
    return asyncio.run(coro)


def make_reasoner(plan_json: str = "[]", synthesis: str = "synthesis"):
    """Return a mock reasoner whose query() returns tuples like ProviderRouter."""
    r = MagicMock()
    r.query = AsyncMock(side_effect=[
        (plan_json, "mock"),
        (synthesis, "mock"),
    ])
    r.query_with_tools = AsyncMock(return_value=({"text": "done", "tool_calls": []}, "mock"))
    return r


# ─── SwarmAgent dataclass ──────────────────────────────────────────────────────

class TestSwarmAgent(unittest.TestCase):
    def test_fields(self):
        a = SwarmAgent(name="TestAgent", instructions="Do stuff")
        self.assertEqual(a.name, "TestAgent")
        self.assertEqual(a.instructions, "Do stuff")
        self.assertEqual(a.tools, [])
        self.assertEqual(a.max_iterations, 5)

    def test_custom_tools_and_iterations(self):
        a = SwarmAgent(name="X", instructions="Y", tools=["bash"], max_iterations=10)
        self.assertEqual(a.tools, ["bash"])
        self.assertEqual(a.max_iterations, 10)


# ─── SubtaskResult dataclass ───────────────────────────────────────────────────

class TestSubtaskResult(unittest.TestCase):
    def test_required_fields(self):
        r = SubtaskResult(subtask="do x", agent_name="Coder", status="done", result="ok")
        self.assertEqual(r.subtask, "do x")
        self.assertEqual(r.agent_name, "Coder")
        self.assertEqual(r.status, "done")
        self.assertEqual(r.result, "ok")

    def test_optional_defaults(self):
        r = SubtaskResult(subtask="s", agent_name="a", status="done", result="r")
        self.assertEqual(r.duration_ms, 0)
        self.assertEqual(r.tool_calls, 0)

    def test_all_status_values(self):
        for status in ("done", "failed", "timeout"):
            r = SubtaskResult(subtask="s", agent_name="a", status=status, result="")
            self.assertEqual(r.status, status)


# ─── SPECIALIST_AGENTS registry ───────────────────────────────────────────────

class TestSpecialistAgents(unittest.TestCase):
    EXPECTED = {"coder", "researcher", "analyst", "sysadmin", "writer"}

    def test_all_present(self):
        self.assertEqual(set(SPECIALIST_AGENTS.keys()), self.EXPECTED)

    def test_all_have_instructions(self):
        for key, agent in SPECIALIST_AGENTS.items():
            self.assertTrue(agent.instructions, f"{key} has empty instructions")

    def test_all_have_tools(self):
        for key, agent in SPECIALIST_AGENTS.items():
            self.assertIsInstance(agent.tools, list, f"{key} tools not a list")
            self.assertGreater(len(agent.tools), 0, f"{key} has no tools")

    def test_all_have_positive_max_iterations(self):
        for key, agent in SPECIALIST_AGENTS.items():
            self.assertGreater(agent.max_iterations, 0, f"{key} max_iterations <= 0")

    def test_all_are_swarm_agents(self):
        for key, agent in SPECIALIST_AGENTS.items():
            self.assertIsInstance(agent, SwarmAgent, f"{key} is not SwarmAgent")


# ─── Swarm.set_reasoner / reasoner property ───────────────────────────────────

class TestSwarmReasonerProperty(unittest.TestCase):
    def test_set_reasoner(self):
        s = Swarm()
        mock = MagicMock()
        s.set_reasoner(mock)
        self.assertIs(s.reasoner, mock)

    def test_lazy_init_skipped_when_set(self):
        s = Swarm()
        mock = MagicMock()
        s.set_reasoner(mock)
        # Should not try to import GroqReasoner
        self.assertIs(s._reasoner, mock)


# ─── Swarm._decompose ─────────────────────────────────────────────────────────

class TestSwarmDecompose(unittest.TestCase):
    def _make_swarm(self, response_text: str) -> Swarm:
        s = Swarm()
        r = MagicMock()
        r.query = AsyncMock(return_value=(response_text, "mock"))
        s.set_reasoner(r)
        return s

    def test_valid_plan_returned(self):
        s = self._make_swarm('[{"subtask": "Write HTML", "agent": "coder"}]')
        result = run(s._decompose("build a website", "", max_agents=5))
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["subtask"], "Write HTML")
        self.assertEqual(result[0]["agent"], "coder")

    def test_empty_array_returns_none(self):
        s = self._make_swarm("[]")
        result = run(s._decompose("task", "", max_agents=5))
        self.assertIsNone(result)

    def test_unknown_agent_defaults_to_coder(self):
        s = self._make_swarm('[{"subtask": "Do research", "agent": "nonexistent_type"}]')
        result = run(s._decompose("task", "", max_agents=5))
        self.assertIsNotNone(result)
        self.assertEqual(result[0]["agent"], "coder")

    def test_plan_capped_at_max_agents(self):
        plan = [{"subtask": f"Task {i}", "agent": "coder"} for i in range(10)]
        import json
        s = self._make_swarm(json.dumps(plan))
        result = run(s._decompose("task", "", max_agents=3))
        self.assertIsNotNone(result)
        self.assertLessEqual(len(result), 3)

    def test_invalid_json_returns_none(self):
        s = self._make_swarm("not valid json at all")
        result = run(s._decompose("task", "", max_agents=5))
        self.assertIsNone(result)

    def test_missing_subtask_key_filtered(self):
        # item without "subtask" key should be filtered out → empty → None
        s = self._make_swarm('[{"agent": "coder"}]')
        result = run(s._decompose("task", "", max_agents=5))
        self.assertIsNone(result)

    def test_code_fenced_json_parsed(self):
        s = self._make_swarm('```json\n[{"subtask": "Write tests", "agent": "analyst"}]\n```')
        result = run(s._decompose("task", "", max_agents=5))
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 1)

    def test_exception_in_reasoner_returns_none(self):
        s = Swarm()
        r = MagicMock()
        r.query = AsyncMock(side_effect=RuntimeError("LLM down"))
        s.set_reasoner(r)
        result = run(s._decompose("task", "", max_agents=5))
        self.assertIsNone(result)

    def test_multiple_agents_all_valid(self):
        plan = [
            {"subtask": "Write backend", "agent": "coder"},
            {"subtask": "Research APIs", "agent": "researcher"},
            {"subtask": "Analyze code", "agent": "analyst"},
        ]
        import json
        s = self._make_swarm(json.dumps(plan))
        result = run(s._decompose("task", "", max_agents=5))
        self.assertEqual(len(result), 3)
        agents = [r["agent"] for r in result]
        self.assertIn("researcher", agents)
        self.assertIn("analyst", agents)


# ─── Swarm._run_single ────────────────────────────────────────────────────────

class TestSwarmRunSingle(unittest.TestCase):
    def test_done_result_returns_string(self):
        s = Swarm()
        done = SubtaskResult(subtask="t", agent_name="Coder", status="done", result="my output")
        with patch.object(s, "_run_subtask", new=AsyncMock(return_value=done)):
            result = run(s._run_single("task", "ctx", 30))
        self.assertEqual(result, "my output")

    def test_failed_result_includes_status_prefix(self):
        s = Swarm()
        failed = SubtaskResult(subtask="t", agent_name="Coder", status="failed", result="err msg")
        with patch.object(s, "_run_subtask", new=AsyncMock(return_value=failed)):
            result = run(s._run_single("task", "ctx", 30))
        self.assertIn("[failed]", result)
        self.assertIn("err msg", result)

    def test_timeout_result_includes_status_prefix(self):
        s = Swarm()
        timed = SubtaskResult(subtask="t", agent_name="Coder", status="timeout", result="Timed out")
        with patch.object(s, "_run_subtask", new=AsyncMock(return_value=timed)):
            result = run(s._run_single("task", "ctx", 30))
        self.assertIn("[timeout]", result)


# ─── Swarm._aggregate ─────────────────────────────────────────────────────────

class TestSwarmAggregate(unittest.TestCase):
    def test_partial_failure_uses_fallback_header(self):
        s = Swarm()
        results = [
            SubtaskResult("t1", "Coder", "done", "output1"),
            SubtaskResult("t2", "Coder", "failed", "error"),
        ]
        report = run(s._aggregate("my task", results))
        self.assertIn("1/2", report)  # 1 succeeded out of 2

    def test_all_done_single_result_uses_fallback(self):
        # only >1 results triggers LLM synthesis
        s = Swarm()
        results = [SubtaskResult("t1", "Coder", "done", "result")]
        # no reasoner set → synthesis skipped
        report = run(s._aggregate("my task", results))
        self.assertIn("Swarm completed", report)

    def test_synthesis_exception_falls_back(self):
        s = Swarm()
        r = MagicMock()
        r.query = AsyncMock(side_effect=RuntimeError("LLM error"))
        s.set_reasoner(r)
        results = [
            SubtaskResult("t1", "Coder", "done", "output1"),
            SubtaskResult("t2", "Researcher", "done", "output2"),
        ]
        report = run(s._aggregate("my task", results))
        # Should fall back gracefully
        self.assertIsInstance(report, str)
        self.assertGreater(len(report), 0)

    def test_all_done_multiple_calls_synthesis(self):
        s = Swarm()
        r = MagicMock()
        r.query = AsyncMock(return_value=("synthesized output", "mock"))
        s.set_reasoner(r)
        results = [
            SubtaskResult("t1", "Coder", "done", "output1"),
            SubtaskResult("t2", "Researcher", "done", "output2"),
        ]
        report = run(s._aggregate("my task", results))
        self.assertEqual(report, "synthesized output")

    def test_results_format_includes_agent_and_status(self):
        s = Swarm()
        results = [
            SubtaskResult("t1", "Coder", "done", "my result"),
            SubtaskResult("t2", "Researcher", "failed", "some error"),
        ]
        report = run(s._aggregate("main task", results))
        self.assertIn("Coder", report)
        self.assertIn("Researcher", report)


# ─── Swarm.run — progress callback ────────────────────────────────────────────

class TestSwarmRunProgress(unittest.TestCase):
    def test_progress_callback_fired_on_decompose_failure(self):
        """When decompose returns None, progress should still fire."""
        s = Swarm()
        r = MagicMock()
        r.query = AsyncMock(return_value=("[]", "mock"))
        r.query_with_tools = AsyncMock(return_value=({"text": "done", "tool_calls": []}, "mock"))
        s.set_reasoner(r)

        calls = []
        with patch.object(s, "_run_single", new=AsyncMock(return_value="single result")):
            result = run(s.run("task", on_progress=calls.append))

        self.assertTrue(len(calls) > 0)
        self.assertEqual(result, "single result")

    def test_progress_callback_fired_during_parallel(self):
        """When plan has agents, progress should fire with agent count message."""
        import json
        plan = [{"subtask": "Task A", "agent": "coder"}]
        s = Swarm()
        r = MagicMock()
        r.query = AsyncMock(side_effect=[
            (json.dumps(plan), "mock"),
            ("synthesis", "mock"),
        ])
        s.set_reasoner(r)

        calls = []
        done_result = SubtaskResult("Task A", "Coder", "done", "output A")
        with patch.object(s, "_run_subtask", new=AsyncMock(return_value=done_result)):
            result = run(s.run("task", on_progress=calls.append))

        progress_msgs = " ".join(str(c) for c in calls)
        self.assertIn("1", progress_msgs)


if __name__ == "__main__":
    unittest.main()
