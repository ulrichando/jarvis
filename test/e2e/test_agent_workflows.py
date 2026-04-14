"""E2E tests for multi-step agent workflow scenarios using MockReasoner."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from test.e2e.base import E2EBase, MockReasoner, LoopInspector
from test.e2e.evaluator import OutcomeEvaluator
from src.agent.loop import agent_loop, MAX_ITERATIONS
from src.agent.tools import TOOL_SCHEMAS


_SYSTEM_PROMPT = "You are JARVIS, a helpful assistant."
_eval = OutcomeEvaluator()


class TestAgentWorkflows(E2EBase):

    def test_write_and_verify_file_workflow(self):
        greet_path = self._tmp_path("greet.txt")
        script = [
            {"text": "", "tool_calls": [
                {"name": "write_file", "args": {"path": greet_path, "content": "hello world"}, "id": "tc_1"},
            ]},
            {"text": "", "tool_calls": [
                {"name": "read_file", "args": {"path": greet_path}, "id": "tc_2"},
            ]},
            {"text": "The file contains 'hello world' as expected.", "tool_calls": []},
        ]
        reasoner = MockReasoner(script)
        inspector = LoopInspector()

        response = self._run(agent_loop(
            reasoner=reasoner,
            user_input="Write a file called greet.txt with content 'hello world', then read it back.",
            system_prompt=_SYSTEM_PROMPT,
            tools=TOOL_SCHEMAS,
            on_tool_call=inspector.on_tool_call,
            on_tool_result=inspector.on_tool_result,
        ))

        self.assertTrue(_eval.file_exists(greet_path), "greet.txt should exist")
        self.assertTrue(_eval.file_contains(greet_path, "hello world"), "greet.txt should contain 'hello world'")
        self.assertIn("hello world", response)
        self.assertFalse(inspector.has_loop(), "No tool loops expected")
        self.assertIn("write_file", inspector.tool_names())
        self.assertIn("read_file", inspector.tool_names())

    def test_search_and_grep_workflow(self):
        self._write_tmp("foo.py", "def main():\n    pass\n")
        script = [
            {"text": "", "tool_calls": [
                {"name": "bash", "args": {"command": f"find {self.tmp} -name '*.py'"}, "id": "tc_1"},
            ]},
            {"text": "", "tool_calls": [
                {"name": "grep", "args": {"pattern": "def main", "path": self.tmp}, "id": "tc_2"},
            ]},
            {"text": "Found foo.py with def main.", "tool_calls": []},
        ]
        reasoner = MockReasoner(script)
        inspector = LoopInspector()

        response = self._run(agent_loop(
            reasoner=reasoner,
            user_input=f"Find all .py files in {self.tmp} then grep for 'def main'",
            system_prompt=_SYSTEM_PROMPT,
            tools=TOOL_SCHEMAS,
            on_tool_call=inspector.on_tool_call,
            on_tool_result=inspector.on_tool_result,
        ))

        self.assertTrue(_eval.response_addresses_goal(response, ["foo.py", "main", "found"]))
        self.assertFalse(inspector.has_loop(), "No tool loops expected")

    def test_edit_file_workflow(self):
        config_path = self._write_tmp("config.txt", "debug=false\nport=8080\n")
        script = [
            {"text": "", "tool_calls": [
                {"name": "edit_file", "args": {
                    "path": config_path,
                    "old_string": "debug=false",
                    "new_string": "debug=true",
                }, "id": "tc_1"},
            ]},
            {"text": "I've updated debug to true in config.txt", "tool_calls": []},
        ]
        reasoner = MockReasoner(script)
        inspector = LoopInspector()

        self._run(agent_loop(
            reasoner=reasoner,
            user_input="Edit config.txt to change 'debug=false' to 'debug=true'",
            system_prompt=_SYSTEM_PROMPT,
            tools=TOOL_SCHEMAS,
            on_tool_call=inspector.on_tool_call,
            on_tool_result=inspector.on_tool_result,
        ))

        content = self._read_tmp("config.txt")
        self.assertIn("debug=true", content)
        self.assertNotIn("debug=false", content)

    def test_no_tools_for_simple_question(self):
        script = [
            {"text": "2 + 2 = 4", "tool_calls": []},
        ]
        reasoner = MockReasoner(script)
        inspector = LoopInspector()

        response = self._run(agent_loop(
            reasoner=reasoner,
            user_input="What is 2 + 2?",
            system_prompt=_SYSTEM_PROMPT,
            tools=TOOL_SCHEMAS,
            on_tool_call=inspector.on_tool_call,
            on_tool_result=inspector.on_tool_result,
        ))

        self.assertIn("4", response)
        self.assertEqual(len(inspector.tool_names()), 0)
        self.assertEqual(reasoner.iterations, 1)

    def test_graceful_tool_failure_handling(self):
        from src.agent.tools import execute_tool
        bad_path = "/nonexistent/path.txt"
        # Verify the tool actually returns an error
        err_result = execute_tool("read_file", {"path": bad_path})
        self.assertTrue(
            "not found" in err_result.lower() or "error" in err_result.lower(),
            f"Expected error from read_file on nonexistent path, got: {err_result!r}"
        )

        script = [
            {"text": "", "tool_calls": [
                {"name": "read_file", "args": {"path": bad_path}, "id": "tc_1"},
            ]},
            {"text": "The file does not exist. I couldn't read it.", "tool_calls": []},
        ]
        reasoner = MockReasoner(script)
        inspector = LoopInspector()

        response = self._run(agent_loop(
            reasoner=reasoner,
            user_input=f"Read the file at {bad_path}",
            system_prompt=_SYSTEM_PROMPT,
            tools=TOOL_SCHEMAS,
            on_tool_call=inspector.on_tool_call,
            on_tool_result=inspector.on_tool_result,
        ))

        self.assertTrue(
            "not exist" in response.lower() or "couldn't" in response.lower(),
            f"Response should mention failure, got: {response!r}"
        )
        self.assertFalse(inspector.has_loop(), "No tool loops on failure")

    def test_readonly_mode_blocks_writes(self):
        secret_path = self._tmp_path("secret.txt")
        script = [
            {"text": "", "tool_calls": [
                {"name": "write_file", "args": {"path": secret_path, "content": "sensitive"}, "id": "tc_1"},
            ]},
            {"text": "Done", "tool_calls": []},
        ]
        reasoner = MockReasoner(script)

        self._run(agent_loop(
            reasoner=reasoner,
            user_input="Write sensitive data to secret.txt",
            system_prompt=_SYSTEM_PROMPT,
            tools=TOOL_SCHEMAS,
            readonly=True,
        ))

        self.assertFalse(os.path.exists(secret_path), "write_file must be blocked in readonly mode")

    def test_iteration_limit_respected(self):
        # Script with many bash calls — more than max_iterations=5
        script = [
            {"text": "", "tool_calls": [
                {"name": "bash", "args": {"command": "echo loop"}, "id": f"tc_{i}"},
            ]}
            for i in range(45)
        ]
        reasoner = MockReasoner(script)
        inspector = LoopInspector()

        self._run(agent_loop(
            reasoner=reasoner,
            user_input="Keep running bash in a loop",
            system_prompt=_SYSTEM_PROMPT,
            tools=TOOL_SCHEMAS,
            max_iterations=5,
            on_tool_call=inspector.on_tool_call,
            on_tool_result=inspector.on_tool_result,
        ))

        self.assertLessEqual(inspector.call_count("bash"), 5, "bash should not exceed max_iterations")

    def test_multi_step_bash_pipeline(self):
        mydir = self._tmp_path("mydir")
        data_file = os.path.join(mydir, "data.txt")
        script = [
            {"text": "", "tool_calls": [
                {"name": "bash", "args": {"command": f"mkdir -p {mydir}"}, "id": "tc_1"},
            ]},
            {"text": "", "tool_calls": [
                {"name": "bash", "args": {"command": f"echo 'data' > {data_file}"}, "id": "tc_2"},
            ]},
            {"text": "", "tool_calls": [
                {"name": "bash", "args": {"command": f"ls {mydir}"}, "id": "tc_3"},
            ]},
            {"text": "Created mydir with data.txt inside.", "tool_calls": []},
        ]
        reasoner = MockReasoner(script)
        inspector = LoopInspector()

        response = self._run(agent_loop(
            reasoner=reasoner,
            user_input="Create a directory, write a file in it, then list its contents",
            system_prompt=_SYSTEM_PROMPT,
            tools=TOOL_SCHEMAS,
            on_tool_call=inspector.on_tool_call,
            on_tool_result=inspector.on_tool_result,
        ))

        self.assertTrue(os.path.isfile(data_file), "data.txt should exist in mydir")
        self.assertTrue(_eval.response_addresses_goal(response, ["data.txt", "mydir", "created"]))
