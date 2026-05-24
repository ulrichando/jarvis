"""Tests for the first batch of ported registry tools: todo, schedule, execute_code.

Proves each ported tool:
  (a) self-registers in registry.all_entries() after import,
  (b) produces a valid RawFunctionTool via load_all_livekit_tools(),
  (c) behaves correctly in smoke tests (no network, no external services).

All tests run against the local voice-agent .venv and need no credentials.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure the voice-agent root is importable.
_VA_ROOT = Path(__file__).resolve().parent.parent
if str(_VA_ROOT) not in sys.path:
    sys.path.insert(0, str(_VA_ROOT))

from livekit.agents.llm import is_raw_function_tool  # noqa: E402
from tools import _adapter as adapter  # noqa: E402
from tools.registry import registry, discover_builtin_tools  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _invoke(tool, args: dict):
    return _run(tool(raw_arguments=args))


# ---------------------------------------------------------------------------
# (a) self-registration
# ---------------------------------------------------------------------------

class TestSelfRegistration:
    """After importing the tool module, registry.all_entries() must include it."""

    def test_todo_registers(self):
        import tools.todo  # noqa: F401 — side effect: registers 'todo'
        assert registry.get_entry("todo") is not None

    def test_schedule_registers(self):
        import tools.schedule  # noqa: F401
        assert registry.get_entry("schedule") is not None

    def test_execute_code_registers(self):
        import tools.execute_code  # noqa: F401
        assert registry.get_entry("execute_code") is not None

    def test_all_three_in_all_entries(self):
        import tools.todo, tools.schedule, tools.execute_code  # noqa: F401
        names = {e.name for e in registry.all_entries()}
        assert "todo" in names
        assert "schedule" in names
        assert "execute_code" in names


# ---------------------------------------------------------------------------
# (b) load_all_livekit_tools returns valid RawFunctionTools
# ---------------------------------------------------------------------------

class TestLivekitAdaptation:
    """Adapted tools must be is_raw_function_tool and carry the correct name."""

    @pytest.fixture(scope="class", autouse=True)
    def _ensure_imports(self):
        import tools.todo, tools.schedule, tools.execute_code  # noqa: F401

    def test_adapted_tools_are_raw_function_tools(self):
        tools = adapter.load_all_livekit_tools()
        assert all(is_raw_function_tool(t) for t in tools), \
            "All adapted tools must be RawFunctionTool instances"

    def _get_adapted(self, name: str):
        tools = adapter.load_all_livekit_tools()
        matched = [t for t in tools if t.info.name == name]
        return matched[0] if matched else None

    def test_todo_adapted(self):
        tool = self._get_adapted("todo")
        # check_fn=None so always present
        assert tool is not None, "'todo' not found in adapted tools"
        assert is_raw_function_tool(tool)

    def test_schedule_adapted_when_pipeline_importable(self):
        # schedule has check_fn that verifies pipeline.cron_jobs is importable.
        # Skip if pipeline is not available in this test environment.
        try:
            from pipeline import cron_jobs  # noqa: F401
        except ImportError:
            pytest.skip("pipeline.cron_jobs not importable in this env")
        tool = self._get_adapted("schedule")
        assert tool is not None, "'schedule' not found in adapted tools"
        assert is_raw_function_tool(tool)

    def test_execute_code_adapted_on_posix(self):
        import platform
        if platform.system() == "Windows":
            pytest.skip("execute_code is POSIX-only")
        tool = self._get_adapted("execute_code")
        assert tool is not None, "'execute_code' not found in adapted tools"
        assert is_raw_function_tool(tool)


# ---------------------------------------------------------------------------
# (c) behavior smoke tests
# ---------------------------------------------------------------------------

class TestTodoBehavior:
    """Smoke tests for the todo tool handler."""

    @pytest.fixture(autouse=True)
    def _reset_store(self):
        """Clear the module-level TodoStore before each test."""
        import tools.todo as _m
        _m._store._items.clear()
        yield
        _m._store._items.clear()

    def _call(self, args: dict) -> dict:
        from tools.todo import _handle_todo
        raw = _handle_todo(args)
        return json.loads(raw)

    def test_read_empty_returns_empty_list(self):
        result = self._call({})
        assert result["todos"] == []
        assert result["summary"]["total"] == 0

    def test_write_replaces_list(self):
        self._call({"todos": [{"id": "1", "content": "Task A", "status": "pending"}]})
        result = self._call({})
        assert len(result["todos"]) == 1
        assert result["todos"][0]["id"] == "1"
        assert result["todos"][0]["content"] == "Task A"

    def test_write_normalizes_invalid_status(self):
        result = self._call({"todos": [{"id": "x", "content": "c", "status": "flying"}]})
        assert result["todos"][0]["status"] == "pending"

    def test_merge_updates_existing(self):
        self._call({"todos": [{"id": "1", "content": "A", "status": "pending"}]})
        result = self._call({"todos": [{"id": "1", "content": "A", "status": "completed"}], "merge": True})
        assert result["todos"][0]["status"] == "completed"
        assert len(result["todos"]) == 1

    def test_merge_appends_new(self):
        self._call({"todos": [{"id": "1", "content": "A", "status": "pending"}]})
        result = self._call({"todos": [{"id": "2", "content": "B", "status": "pending"}], "merge": True})
        assert len(result["todos"]) == 2
        ids = {t["id"] for t in result["todos"]}
        assert ids == {"1", "2"}

    def test_replace_clears_old(self):
        self._call({"todos": [{"id": "1", "content": "A", "status": "pending"}]})
        result = self._call({"todos": [{"id": "2", "content": "B", "status": "pending"}], "merge": False})
        assert len(result["todos"]) == 1
        assert result["todos"][0]["id"] == "2"

    def test_summary_counts(self):
        todos = [
            {"id": "1", "content": "a", "status": "pending"},
            {"id": "2", "content": "b", "status": "in_progress"},
            {"id": "3", "content": "c", "status": "completed"},
            {"id": "4", "content": "d", "status": "cancelled"},
        ]
        result = self._call({"todos": todos})
        s = result["summary"]
        assert s["pending"] == 1
        assert s["in_progress"] == 1
        assert s["completed"] == 1
        assert s["cancelled"] == 1
        assert s["total"] == 4

    def test_invalid_todos_type_returns_error(self):
        result = self._call({"todos": "not-a-list"})
        assert "error" in json.loads(result) if isinstance(result, str) else "error" in result

    def test_format_for_injection_only_active(self):
        import tools.todo as _m
        _m._store._items.clear()
        _m._store.write([
            {"id": "1", "content": "done", "status": "completed"},
            {"id": "2", "content": "active", "status": "in_progress"},
        ])
        out = _m._store.format_for_injection()
        assert out is not None
        assert "active" in out
        assert "done" not in out


class TestScheduleBehavior:
    """Smoke tests for the schedule tool handler."""

    @pytest.fixture(autouse=True)
    def _patch_pipeline(self, tmp_path, monkeypatch):
        """Redirect cron_jobs file I/O to a tmp dir for isolation."""
        try:
            from pipeline import cron_jobs as cj
        except ImportError:
            pytest.skip("pipeline.cron_jobs not importable")

        # Redirect CRON_DIR, JOBS_FILE, OUTPUT_DIR to tmp
        monkeypatch.setattr(cj, "CRON_DIR", tmp_path)
        monkeypatch.setattr(cj, "JOBS_FILE", tmp_path / "jobs.json")
        monkeypatch.setattr(cj, "OUTPUT_DIR", tmp_path / "output")
        (tmp_path / "output").mkdir()
        yield

    def _call(self, args: dict) -> dict:
        from tools.schedule import _handle_schedule
        raw = _handle_schedule(args)
        if isinstance(raw, str):
            return json.loads(raw)
        return raw

    def test_list_empty(self):
        result = self._call({"action": "list"})
        assert result["success"] is True
        assert result["jobs"] == []

    def test_create_prompt_job(self):
        result = self._call({
            "action": "create",
            "name": "Daily briefing",
            "schedule": "every 60m",
            "type": "prompt",
            "prompt": "Summarize the latest news.",
        })
        assert result["success"] is True
        assert "id" in result["job"]
        assert result["job"]["name"] == "Daily briefing"

    def test_create_requires_name(self):
        result = self._call({"action": "create", "schedule": "every 60m", "prompt": "x"})
        assert "error" in result

    def test_create_requires_schedule(self):
        result = self._call({"action": "create", "name": "x", "prompt": "y"})
        assert "error" in result

    def test_create_prompt_requires_prompt(self):
        result = self._call({"action": "create", "name": "x", "schedule": "every 60m"})
        assert "error" in result

    def test_list_after_create(self):
        self._call({"action": "create", "name": "Job1", "schedule": "every 60m",
                    "type": "prompt", "prompt": "test"})
        result = self._call({"action": "list"})
        assert result["count"] == 1

    def test_remove_job(self):
        created = self._call({"action": "create", "name": "Job2", "schedule": "every 60m",
                               "type": "prompt", "prompt": "test"})
        job_id = created["job"]["id"]
        result = self._call({"action": "remove", "job_id": job_id})
        assert result["success"] is True
        listed = self._call({"action": "list"})
        assert listed["count"] == 0

    def test_remove_nonexistent_returns_error(self):
        result = self._call({"action": "remove", "job_id": "doesnotexist"})
        assert "error" in result

    def test_pause_and_resume(self):
        created = self._call({"action": "create", "name": "Job3", "schedule": "every 60m",
                               "type": "prompt", "prompt": "test"})
        job_id = created["job"]["id"]
        pause_result = self._call({"action": "pause", "job_id": job_id})
        assert pause_result["success"] is True
        resume_result = self._call({"action": "resume", "job_id": job_id})
        assert resume_result["success"] is True

    def test_unknown_action_returns_error(self):
        result = self._call({"action": "blorp"})
        assert "error" in result


class TestExecuteCodeBehavior:
    """Smoke tests for execute_code. Require POSIX and working subprocess."""

    @pytest.fixture(autouse=True)
    def _skip_on_windows(self):
        import platform
        if platform.system() == "Windows":
            pytest.skip("execute_code is POSIX-only")

    def _call(self, code: str) -> dict:
        from tools.execute_code import execute_code
        raw = execute_code(code)
        return json.loads(raw)

    def test_print_hello(self):
        result = self._call("print('hello from sandbox')")
        assert result["status"] == "success"
        assert "hello from sandbox" in result["output"]

    def test_stdout_captured(self):
        result = self._call("for i in range(5): print(i)")
        assert result["status"] == "success"
        for i in range(5):
            assert str(i) in result["output"]

    def test_syntax_error_returns_error_status(self):
        result = self._call("def broken(")
        assert result["status"] == "error"

    def test_runtime_error_returns_error_status(self):
        result = self._call("raise ValueError('test error')")
        assert result["status"] == "error"

    def test_empty_code_returns_tool_error(self):
        from tools.execute_code import execute_code
        raw = execute_code("   ")
        # tool_error returns JSON with "error" key
        data = json.loads(raw)
        assert "error" in data

    def test_duration_is_positive(self):
        result = self._call("import time; time.sleep(0.01); print('ok')")
        assert result.get("duration_seconds", 0) > 0

    def test_tool_calls_made_zero_for_no_rpc(self):
        result = self._call("print('no tools called')")
        assert result["tool_calls_made"] == 0

    def test_multiline_script(self):
        code = (
            "import json\n"
            "data = {'key': 'value', 'nums': [1, 2, 3]}\n"
            "print(json.dumps(data))\n"
        )
        result = self._call(code)
        assert result["status"] == "success"
        parsed = json.loads(result["output"].strip())
        assert parsed["key"] == "value"

    def test_file_write_then_read_via_stdlib(self):
        """Verify the sandbox can write+read files using stdlib (no RPC)."""
        code = (
            "import tempfile, os\n"
            "with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:\n"
            "    f.write('jarvis sandbox ok')\n"
            "    fname = f.name\n"
            "with open(fname, encoding='utf-8') as f:\n"
            "    print(f.read())\n"
            "os.unlink(fname)\n"
        )
        result = self._call(code)
        assert result["status"] == "success"
        assert "jarvis sandbox ok" in result["output"]

    def test_timeout_produces_timeout_status(self):
        """A script that sleeps longer than the timeout must be killed."""
        from tools import execute_code as ec_mod
        import unittest.mock as mock

        # Patch DEFAULT_TIMEOUT to 1s so the test doesn't actually wait 300s
        with mock.patch.object(ec_mod, "DEFAULT_TIMEOUT", 1):
            from tools.execute_code import execute_code as ec
            result = json.loads(ec("import time; time.sleep(10); print('done')"))
        assert result["status"] == "timeout"
        assert "error" in result


# ---------------------------------------------------------------------------
# Cross-cutting: grepping new files for "hermes" (belt-and-suspenders)
# ---------------------------------------------------------------------------

class TestNoHermesTokens:
    """Static check: none of the new tool files contain the string 'hermes'."""

    @pytest.mark.parametrize("fname", ["todo.py", "schedule.py", "execute_code.py"])
    def test_no_hermes_in_file(self, fname):
        path = _VA_ROOT / "tools" / fname
        text = path.read_text(encoding="utf-8").lower()
        # The one allowed occurrence is "hermes" in a comment about what was
        # scrubbed — this test itself is the belt-and-suspenders check.
        # The only acceptable hit would be in a comment saying "scrubbed";
        # guard by checking the full token appears nowhere except as a comment.
        lines = path.read_text(encoding="utf-8").splitlines()
        bad_lines = []
        for lineno, line in enumerate(lines, 1):
            if "hermes" in line.lower():
                # Allow a comment that documents the scrub
                stripped = line.lstrip()
                if stripped.startswith("#") and "hermes" in stripped.lower():
                    continue  # comment explaining the port — acceptable
                if '"""' in line and "hermes" in line.lower():
                    continue  # docstring explaining the port — acceptable
                bad_lines.append((lineno, line.rstrip()))
        assert not bad_lines, (
            f"File {fname} contains non-comment 'hermes' tokens:\n"
            + "\n".join(f"  line {ln}: {txt}" for ln, txt in bad_lines)
        )
