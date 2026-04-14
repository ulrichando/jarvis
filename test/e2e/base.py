"""
E2E test base infrastructure.

MockReasoner: plays back a scripted list of (text, tool_calls) responses.
E2EBase: unittest base class with temp dir and helpers.
LoopInspector: records every tool call + result during agent_loop execution.
"""

import asyncio
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))


class MockReasoner:
    """Plays back a scripted list of LLM responses — no real LLM required."""

    model = "mock-llm"
    active_model_name = "mock-llm"

    def __init__(self, script: list[dict]):
        """
        script: list of dicts, each with:
            "text": str          (assistant text content)
            "tool_calls": list   (list of {name, args, id} dicts)
        """
        self._script = script
        self._index = 0
        self.calls_made = 0
        self.total_tool_calls: list[str] = []
        self.iterations = 0

    async def query_with_tools(self, messages: list[dict], tools: list[dict], force_tool: bool = False) -> dict:
        self.calls_made += 1
        self.iterations += 1

        if self._index < len(self._script):
            entry = self._script[self._index]
            self._index += 1
        else:
            return {"text": "Done.", "tool_calls": [], "usage": {}}

        text = entry.get("text", "")
        tool_calls = entry.get("tool_calls", [])

        for tc in tool_calls:
            self.total_tool_calls.append(tc.get("name", "unknown"))

        return {"text": text, "tool_calls": tool_calls, "usage": {}}


class LoopInspector:
    """Records tool calls and results during agent_loop execution."""

    def __init__(self):
        self._calls: list[dict] = []
        self._results: list[dict] = []

    def on_tool_call(self, name: str, args: dict):
        self._calls.append({"name": name, "args": args})

    def on_tool_result(self, name: str, result: str):
        self._results.append({"name": name, "result": result})

    def tool_names(self) -> list[str]:
        return [c["name"] for c in self._calls]

    def call_count(self, name: str) -> int:
        return sum(1 for c in self._calls if c["name"] == name)

    def has_loop(self, max_same: int = 3) -> bool:
        from collections import Counter
        counts = Counter(self._calls[i]["name"] for i in range(len(self._calls)))
        return any(v > max_same for v in counts.values())

    def all_succeeded(self) -> bool:
        for r in self._results:
            result = r.get("result", "")
            if isinstance(result, str) and (
                result.startswith("ERROR:") or result.startswith("BLOCKED:")
            ):
                return False
        return True


class E2EBase(unittest.TestCase):
    """Base class for E2E tests — creates a temp dir and provides helpers."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="jarvis_e2e_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, coro):
        return asyncio.run(coro)

    def _tmp_path(self, name: str) -> str:
        return os.path.join(self.tmp, name)

    def _write_tmp(self, name: str, content: str) -> str:
        path = self._tmp_path(name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
        return path

    def _read_tmp(self, name: str) -> str:
        path = self._tmp_path(name)
        with open(path) as f:
            return f.read()
