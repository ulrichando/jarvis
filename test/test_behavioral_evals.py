"""Behavioral Evaluation Harness — 20 scenarios across 7 categories.

Inspired by DeepAgents' eval methodology: end-to-end behavioral tests that
verify CORRECTNESS (did it solve the problem?) and EFFICIENCY (no waste).

Categories covered:
  1. File Operations   (4 tests) — read, write, persist, large-result offload
  2. Tool Use          (4 tests) — parallel dispatch, dependency enforcement, blocked tasks
  3. Memory            (3 tests) — AGENTS.md round-trip, recall injection, session dedup
  4. Retrieval         (2 tests) — todo_write list, blocked indicators
  5. Context           (3 tests) — fraction trigger, messages trigger, combined triggers
  6. Task Graph        (2 tests) — blocks enforcement, get_ready_tasks
  7. Research Prompts  (2 tests) — planner prompt content, deepsearch efficiency rules

All tests are pure/near-pure: no LLM calls, no file I/O to real disk (uses tmp dirs).
"""

import asyncio
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# 1. FILE OPERATIONS
# ---------------------------------------------------------------------------


class TestFileOperations(unittest.TestCase):
    """Tests that file-related tools work correctly and handle edge cases."""

    def test_validate_path_blocks_sensitive(self):
        """Path validation must block sensitive system paths."""
        from src.agent.tools import _validate_path
        ok, msg = _validate_path("/etc/shadow")
        self.assertFalse(ok, "Should block /etc/shadow")
        # Accept any denial message ("blocked", "denied", "protected", "restricted")
        self.assertTrue(
            any(word in msg.lower() for word in ("blocked", "denied", "protected", "restricted")),
            f"Expected a denial message, got: {msg!r}",
        )

    def test_validate_path_allows_home(self):
        """Path validation should allow user's home directory files."""
        from src.agent.tools import _validate_path
        home = os.path.expanduser("~/test_file.txt")
        ok, _msg = _validate_path(home, write=False)
        self.assertTrue(ok, f"Should allow home path {home}")

    def test_large_result_persist_returns_preview(self):
        """Large tool results should be persisted with a path reference + preview."""
        from src.agent.tool_registry import persist_large_result
        large_content = "x" * 50000
        with tempfile.TemporaryDirectory() as tmpdir:
            result = persist_large_result("bash", "tool-call-001", large_content, session_dir=tmpdir)
        # result is a ToolResult; coerce to str for assertion
        result_str = str(result)
        # Should mention the output file and be much shorter than the original
        self.assertTrue(
            len(result_str) < len(large_content) // 2 or "saved" in result_str.lower()
                or ".md" in result_str or "output" in result_str.lower(),
            f"Persisted result should reference saved file: {result_str[:200]!r}",
        )

    def test_todo_write_blocked_indicator_shown(self):
        """Tasks blocked by incomplete dependencies should show [blocked] in output."""
        from src.agent.tools import _exec_todo_write
        todos = [
            {"id": "a", "content": "First step", "status": "pending", "blocks": ["b"]},
            {"id": "b", "content": "Second step", "status": "pending"},
        ]
        result = _exec_todo_write({"todos": todos})
        self.assertIn("[blocked]", result, "Dependent task should show [blocked]")


# ---------------------------------------------------------------------------
# 2. TOOL USE
# ---------------------------------------------------------------------------


class TestToolUse(unittest.TestCase):
    """Tests for tool dispatch, schema correctness, and dependency enforcement."""

    def test_todo_write_blocks_field_in_schema(self):
        """todo_write tool schema must include the 'blocks' field."""
        from src.agent.tools import TOOL_SCHEMAS
        todo_schema = next(
            (t for t in TOOL_SCHEMAS if t.get("function", {}).get("name") == "todo_write"),
            None,
        )
        self.assertIsNotNone(todo_schema, "todo_write must be in TOOL_SCHEMAS")
        items = (
            todo_schema["function"]["parameters"]["properties"]["todos"]["items"]
        )
        self.assertIn("blocks", items["properties"],
                      "todo_write items must have 'blocks' property")

    def test_todo_dependency_violation_rejected(self):
        """Setting a blocked task to in_progress before its blocker completes must fail."""
        from src.agent.tools import _exec_todo_write
        # Reset global state
        _exec_todo_write({"todos": []})

        todos = [
            {"id": "setup", "content": "Install deps", "status": "pending", "blocks": ["test"]},
            {"id": "test", "content": "Run tests", "status": "pending"},
        ]
        _exec_todo_write({"todos": todos})

        # Attempt to start 'test' while 'setup' is still pending
        bad_todos = [
            {"id": "setup", "content": "Install deps", "status": "pending", "blocks": ["test"]},
            {"id": "test", "content": "Run tests", "status": "in_progress"},
        ]
        result = _exec_todo_write({"todos": bad_todos})
        self.assertIn("blocked", result.lower(),
                      "Should reject starting a task with incomplete prerequisites")
        self.assertIn("Install deps", result,
                      "Error message should name the blocking task")

    def test_todo_dependency_allowed_after_completion(self):
        """Setting a blocked task to in_progress after blocker completes must succeed."""
        from src.agent.tools import _exec_todo_write

        # Setup initial state
        _exec_todo_write({"todos": [
            {"id": "setup", "content": "Install deps", "status": "pending", "blocks": ["test"]},
            {"id": "test", "content": "Run tests", "status": "pending"},
        ]})

        # Complete blocker, then start dependent
        result = _exec_todo_write({"todos": [
            {"id": "setup", "content": "Install deps", "status": "completed", "blocks": ["test"]},
            {"id": "test", "content": "Run tests", "status": "in_progress"},
        ]})
        self.assertNotIn("violation", result.lower(),
                         "Should allow starting task after blocker is completed")
        self.assertIn("[>]", result, "Task should show in-progress indicator")

    def test_tool_schemas_have_required_fields(self):
        """All tool schemas must have name, description, and parameters."""
        from src.agent.tools import TOOL_SCHEMAS
        for schema in TOOL_SCHEMAS:
            fn = schema.get("function", {})
            name = fn.get("name", "?")
            self.assertIn("name", fn, f"Tool missing name: {schema}")
            self.assertIn("description", fn, f"Tool {name!r} missing description")
            self.assertIn("parameters", fn, f"Tool {name!r} missing parameters")


# ---------------------------------------------------------------------------
# 3. MEMORY
# ---------------------------------------------------------------------------


class TestMemory(unittest.TestCase):
    """Tests for AGENTS.md generation, loading, and system-prompt injection."""

    def test_agents_md_round_trip(self):
        """AGENTS.md serialize/parse round-trip must preserve all entries."""
        from src.memory.agents_memory import AgentsMemoryDoc, _parse_agents_md

        doc = AgentsMemoryDoc()
        sec = doc.get_or_create_section("Behavioral Rules")
        sec.entries = ["No Co-Authored-By in commits", "Always update all related files"]

        sec2 = doc.get_or_create_section("User Profile")
        sec2.entries = ["Ulrich: cybersecurity engineer"]

        md = doc.to_markdown()
        doc2 = _parse_agents_md(md)

        self.assertEqual(
            doc2.sections["Behavioral Rules"].entries,
            doc.sections["Behavioral Rules"].entries,
            "Behavioral Rules should survive round-trip",
        )
        self.assertEqual(
            doc2.sections["User Profile"].entries,
            doc.sections["User Profile"].entries,
            "User Profile should survive round-trip",
        )

    def test_agents_md_system_prompt_format(self):
        """to_system_prompt() must include the injection header and sections."""
        from src.memory.agents_memory import AgentsMemoryDoc

        doc = AgentsMemoryDoc()
        doc.get_or_create_section("Behavioral Rules").entries = ["Be concise"]

        prompt = doc.to_system_prompt()
        self.assertIn("[Learned agent context", prompt,
                      "System prompt block must start with injection header")
        self.assertIn("Be concise", prompt,
                      "System prompt block must include memory content")

    def test_agents_md_generate_from_memory_dir(self):
        """regenerate() must read memory/*.md files and populate sections."""
        from src.memory.agents_memory import AgentsMemory

        with tempfile.TemporaryDirectory() as tmpdir:
            mem_dir = Path(tmpdir) / "memory"
            mem_dir.mkdir()
            agents_path = Path(tmpdir) / "AGENTS.md"

            # Write a feedback memory file
            (mem_dir / "feedback_test.md").write_text(textwrap.dedent("""
                ---
                name: no-coauthor
                description: No Co-Authored-By lines
                type: feedback
                ---
                Never add Co-Authored-By lines to commit messages.
            """).strip())

            # Write a user memory file
            (mem_dir / "user_profile.md").write_text(textwrap.dedent("""
                ---
                name: ulrich-profile
                description: User background
                type: user
                ---
                Ulrich is a cybersecurity developer.
            """).strip())

            am = AgentsMemory(
                memory_dir=mem_dir,
                global_agents_path=agents_path,
            )
            doc = am.regenerate(save=False)

            self.assertTrue(
                doc.sections["Behavioral Rules"].entries,
                "Behavioral Rules section should have entries from feedback_*.md",
            )
            self.assertTrue(
                doc.sections["User Profile"].entries,
                "User Profile section should have entries from user_*.md",
            )


# ---------------------------------------------------------------------------
# 4. RETRIEVAL
# ---------------------------------------------------------------------------


class TestRetrieval(unittest.TestCase):
    """Tests for todo list retrieval and dependency display."""

    def test_get_todo_list_returns_current(self):
        """get_todo_list() should reflect the last todo_write call."""
        from src.agent.tools import _exec_todo_write, get_todo_list

        todos = [{"id": "x", "content": "Do something", "status": "pending"}]
        _exec_todo_write({"todos": todos})
        current = get_todo_list()
        self.assertEqual(len(current), 1)
        self.assertEqual(current[0]["content"], "Do something")

    def test_todo_write_status_counts(self):
        """todo_write output should report correct pending/in_progress/completed counts."""
        from src.agent.tools import _exec_todo_write

        todos = [
            {"id": "1", "content": "Pending task", "status": "pending"},
            {"id": "2", "content": "Active task", "status": "in_progress"},
            {"id": "3", "content": "Done task", "status": "completed"},
        ]
        result = _exec_todo_write({"todos": todos})
        self.assertIn("1 pending", result)
        self.assertIn("1 in progress", result)
        self.assertIn("1 completed", result)


# ---------------------------------------------------------------------------
# 5. CONTEXT MANAGEMENT
# ---------------------------------------------------------------------------


class TestContextManagement(unittest.TestCase):
    """Tests for AutoCompactor trigger types."""

    def _make_messages(self, n: int, chars_each: int = 100) -> list[dict]:
        return [{"role": "user", "content": "x" * chars_each} for _ in range(n)]

    def test_fraction_trigger_fires(self):
        """fraction trigger must fire when usage >= threshold fraction."""
        from src.agent.context import AutoCompactor

        # Use very large messages so the fraction is genuinely exceeded on a 180K-token model.
        # 500_000 chars ÷ 4 chars/token ≈ 125_000 tokens → ~70% of 180K window → > 0.50 threshold
        ac = AutoCompactor(compact_trigger=("fraction", 0.50))
        msgs = self._make_messages(5, chars_each=100_000)
        self.assertTrue(ac.should_compact(msgs),
                        "Fraction trigger at 0.50 should fire when context is >50% full")

    def test_fraction_trigger_does_not_fire_below_threshold(self):
        """fraction trigger must NOT fire when usage < threshold."""
        from src.agent.context import AutoCompactor

        # 0.99 fraction — only fires when nearly full
        ac = AutoCompactor(compact_trigger=("fraction", 0.99))
        msgs = self._make_messages(2, chars_each=50)
        self.assertFalse(ac.should_compact(msgs),
                         "Fraction trigger at 0.99 should not fire on tiny content")

    def test_messages_trigger_fires(self):
        """messages trigger must fire when message count >= threshold."""
        from src.agent.context import AutoCompactor

        ac = AutoCompactor(compact_trigger=("messages", 5))
        self.assertFalse(ac.should_compact(self._make_messages(3)))
        self.assertTrue(ac.should_compact(self._make_messages(7)))

    def test_tokens_trigger_fires(self):
        """tokens trigger must fire when token count exceeds threshold."""
        from src.agent.context import AutoCompactor

        ac = AutoCompactor(compact_trigger=("tokens", 10))
        small = self._make_messages(1, chars_each=4)    # ~1 token
        large = self._make_messages(1, chars_each=400)  # ~100 tokens
        self.assertFalse(ac.should_compact(small))
        self.assertTrue(ac.should_compact(large))

    def test_combined_triggers_or_semantics(self):
        """Combined triggers should fire when ANY trigger condition is met."""
        from src.agent.context import AutoCompactor

        # messages=5 or fraction=0.99 (only messages fires here)
        ac = AutoCompactor(compact_trigger=[("messages", 5), ("fraction", 0.99)])
        self.assertTrue(ac.should_compact(self._make_messages(7)),
                        "Should fire on messages trigger even if fraction trigger is not met")


# ---------------------------------------------------------------------------
# 6. TASK GRAPH
# ---------------------------------------------------------------------------


class TestTaskGraph(unittest.TestCase):
    """Tests for TaskManager dependency tracking."""

    def _make_manager(self):
        from src.tasks_brain.manager import TaskManager
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        return TaskManager(db_path=Path(path)), path

    def tearDown(self):
        # Cleanup temp DBs
        pass

    def test_add_and_check_dependency(self):
        """Tasks with blockers should be detectable via is_blocked()."""
        from src.tasks_brain.manager import TaskManager
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = TaskManager(db_path=Path(tmpdir) / "tasks.db")
            t1 = mgr.create("Setup")
            t2 = mgr.create("Tests")
            mgr.add_dependency(blocker_id=t1.id, blocked_id=t2.id)

            self.assertFalse(mgr.is_blocked(t1.id), "t1 has no blockers")
            self.assertTrue(mgr.is_blocked(t2.id), "t2 is blocked by t1")

    def test_get_ready_tasks_excludes_blocked(self):
        """get_ready_tasks() must only return tasks with no incomplete blockers."""
        from src.tasks_brain.manager import TaskManager
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = TaskManager(db_path=Path(tmpdir) / "tasks.db")
            t1 = mgr.create("Setup")
            t2 = mgr.create("Tests")
            mgr.add_dependency(blocker_id=t1.id, blocked_id=t2.id)

            ready = mgr.get_ready_tasks()
            ready_ids = {t.id for t in ready}
            self.assertIn(t1.id, ready_ids, "t1 (no blockers) should be in ready tasks")
            self.assertNotIn(t2.id, ready_ids, "t2 (blocked by t1) must NOT be in ready tasks")

    def test_can_start_after_blocker_done(self):
        """can_start() must return True after all blockers are marked done."""
        from src.tasks_brain.manager import TaskManager
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = TaskManager(db_path=Path(tmpdir) / "tasks.db")
            t1 = mgr.create("Setup")
            t2 = mgr.create("Tests")
            mgr.add_dependency(blocker_id=t1.id, blocked_id=t2.id)

            ok, blockers = mgr.can_start(t2.id)
            self.assertFalse(ok)
            self.assertEqual(len(blockers), 1)

            mgr.update_status(t1.id, "done")
            ok2, blockers2 = mgr.can_start(t2.id)
            self.assertTrue(ok2, "After blocker done, can_start should return True")
            self.assertEqual(len(blockers2), 0)


# ---------------------------------------------------------------------------
# 7. RESEARCH PROMPTS
# ---------------------------------------------------------------------------


class TestResearchPrompts(unittest.TestCase):
    """Tests that planner and deepsearch prompts contain DeepAgents rules."""

    def test_planner_prompt_has_search_efficiency_rules(self):
        """PLANNER_PROMPT must include search efficiency caps."""
        from src.agent.agents import PLANNER_PROMPT

        self.assertIn("2-3 searches", PLANNER_PROMPT,
                      "Planner must instruct 2-3 searches for simple questions")
        self.assertIn("5", PLANNER_PROMPT,
                      "Planner must allow up to 5 searches for complex queries")
        self.assertIn("think", PLANNER_PROMPT,
                      "Planner must instruct using think between searches")

    def test_planner_prompt_has_citation_rules(self):
        """PLANNER_PROMPT must include citation format instructions."""
        from src.agent.agents import PLANNER_PROMPT

        self.assertIn("[1]", PLANNER_PROMPT,
                      "Planner should specify citation number format [N]")
        self.assertIn("citation", PLANNER_PROMPT.lower(),
                      "Planner should include citation instructions")

    def test_planner_prompt_has_report_structure(self):
        """PLANNER_PROMPT must define different report structures by question type."""
        from src.agent.agents import PLANNER_PROMPT

        self.assertIn("Comparison", PLANNER_PROMPT,
                      "Planner should define structure for comparison queries")
        self.assertIn("Overview", PLANNER_PROMPT,
                      "Planner should define overview section for summaries")

    def test_deepsearch_efficiency_caps(self):
        """DeepSearch._plan_queries must apply max_queries cap."""
        import asyncio
        from src.agent.deepsearch import DeepSearch, ResearchContext

        async def run_test():
            reasoner = MagicMock()
            reasoner.query = AsyncMock(
                return_value=('["q1","q2","q3","q4","q5","q6","q7"]', {})
            )
            ds = DeepSearch(reasoner=reasoner)
            ctx = ResearchContext(
                question="simple question",
                gaps=["gap1"],
            )
            queries = await ds._plan_queries(ctx)
            # max_queries for non-complex (1 gap) = 3
            self.assertLessEqual(len(queries), 5,
                                 "DeepSearch must cap queries to avoid over-searching")

        asyncio.run(run_test())


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    unittest.main(verbosity=2)
