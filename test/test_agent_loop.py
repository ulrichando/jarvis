"""Tests for src/agent/loop.py — pure/near-pure helper functions.

Covers: _validate_tool_calls, _scrub_identity, _tool_call_sig,
        _is_tool_failure, _append_assistant_message, _append_tool_result.
"""

import json
import os
import sys
import unittest
from unittest.mock import patch

# Ensure project root is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.agent.loop import (
    _validate_tool_calls,
    _scrub_identity,
    _tool_call_sig,
    _is_tool_failure,
    _append_assistant_message,
    _append_tool_result,
)


class TestValidateToolCalls(unittest.TestCase):
    """Tests for _validate_tool_calls() — filtering and normalizing tool calls."""

    def test_valid_tool_call_passes_through(self):
        """A fully valid tool call (name, args, id) should be returned as-is."""
        tc = {"name": "bash", "args": {"command": "ls"}, "id": "tc_001"}
        result = _validate_tool_calls([tc])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "bash")
        self.assertEqual(result[0]["args"], {"command": "ls"})
        self.assertEqual(result[0]["id"], "tc_001")

    def test_missing_name_filtered_out(self):
        """Tool calls without a 'name' key should be dropped."""
        tc = {"args": {"command": "ls"}, "id": "tc_002"}
        result = _validate_tool_calls([tc])
        self.assertEqual(result, [])

    def test_missing_args_defaults_to_empty_dict(self):
        """Tool calls without 'args' should get args defaulted to {}."""
        tc = {"name": "read_file", "id": "tc_003"}
        result = _validate_tool_calls([tc])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["args"], {})

    def test_missing_id_gets_auto_generated(self):
        """Tool calls without 'id' should get an auto-generated id."""
        tc = {"name": "write_file", "args": {"path": "/tmp/x"}}
        result = _validate_tool_calls([tc])
        self.assertEqual(len(result), 1)
        self.assertIn("id", result[0])
        self.assertTrue(result[0]["id"].startswith("tc_"))

    def test_non_dict_items_filtered(self):
        """Non-dict items in the list should be skipped."""
        result = _validate_tool_calls(["not_a_dict", 42, None, True])
        self.assertEqual(result, [])

    def test_empty_list(self):
        """An empty input list should return an empty list."""
        result = _validate_tool_calls([])
        self.assertEqual(result, [])

    def test_mixed_valid_and_invalid(self):
        """Only valid tool calls should survive filtering."""
        calls = [
            {"name": "bash", "args": {"command": "echo hi"}, "id": "tc_1"},
            {"args": {"command": "ls"}},               # missing name
            "garbage",                                   # non-dict
            {"name": "read_file"},                       # valid, missing args/id
            42,                                          # non-dict
        ]
        result = _validate_tool_calls(calls)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["name"], "bash")
        self.assertEqual(result[1]["name"], "read_file")
        self.assertEqual(result[1]["args"], {})
        self.assertIn("id", result[1])

    def test_preserves_original_args(self):
        """Existing args should not be overwritten by the default."""
        tc = {"name": "bash", "args": {"command": "pwd"}, "id": "tc_10"}
        result = _validate_tool_calls([tc])
        self.assertEqual(result[0]["args"], {"command": "pwd"})

    def test_multiple_valid_calls(self):
        """Multiple valid tool calls should all pass through."""
        calls = [
            {"name": "bash", "args": {}, "id": "a"},
            {"name": "read_file", "args": {"path": "/tmp"}, "id": "b"},
            {"name": "think", "args": {"thought": "hmm"}, "id": "c"},
        ]
        result = _validate_tool_calls(calls)
        self.assertEqual(len(result), 3)

    def test_mutates_in_place_with_setdefault(self):
        """setdefault should add 'args' and 'id' to the original dict."""
        tc = {"name": "bash"}
        result = _validate_tool_calls([tc])
        # The original dict object is mutated by setdefault
        self.assertIn("args", tc)
        self.assertIn("id", tc)

    def test_name_only_minimal_call(self):
        """A tool call with only 'name' should still pass with defaults."""
        result = _validate_tool_calls([{"name": "think"}])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "think")
        self.assertEqual(result[0]["args"], {})
        self.assertTrue(result[0]["id"].startswith("tc_"))

    def test_empty_name_still_passes(self):
        """A tool call with empty string name still has 'name' key, so it passes."""
        result = _validate_tool_calls([{"name": ""}])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "")


class TestScrubIdentity(unittest.TestCase):
    """Tests for _scrub_identity() — replacing Claude/Anthropic identity leaks."""

    def test_im_claude(self):
        """'I'm Claude' should become 'I'm JARVIS'."""
        self.assertIn("I'm JARVIS", _scrub_identity("I'm Claude"))

    def test_i_am_claude(self):
        """'I am Claude' should become 'I am JARVIS'."""
        self.assertIn("I am JARVIS", _scrub_identity("I am Claude"))

    def test_created_by_anthropic(self):
        """'created by Anthropic' should become 'built by Ulrich'."""
        result = _scrub_identity("I was created by Anthropic")
        self.assertIn("built by Ulrich", result)
        self.assertNotIn("Anthropic", result)

    def test_made_by_anthropic(self):
        """'made by Anthropic' should become 'built by Ulrich'."""
        result = _scrub_identity("made by Anthropic")
        self.assertIn("built by Ulrich", result)

    def test_no_identity_leaks(self):
        """Text with no identity references should be unchanged."""
        original = "The weather today is sunny and warm."
        self.assertEqual(_scrub_identity(original), original)

    def test_empty_string(self):
        """Empty string should be returned as-is."""
        self.assertEqual(_scrub_identity(""), "")

    def test_none_input(self):
        """None input should be returned as-is (falsy passthrough)."""
        self.assertIsNone(_scrub_identity(None))

    def test_claude_standalone(self):
        """Standalone 'Claude' should become 'JARVIS'."""
        result = _scrub_identity("Hello, Claude here.")
        self.assertIn("JARVIS", result)
        self.assertNotIn("Claude", result)

    def test_anthropic_standalone(self):
        """Standalone 'Anthropic' should become 'Ulrich'."""
        result = _scrub_identity("Anthropic makes great models")
        self.assertIn("Ulrich", result)
        self.assertNotIn("Anthropic", result)

    def test_my_name_is_claude(self):
        """'my name is Claude' should become 'my name is JARVIS'."""
        result = _scrub_identity("my name is Claude")
        self.assertIn("my name is JARVIS", result)

    def test_case_insensitive(self):
        """Substitution should work regardless of case."""
        result = _scrub_identity("i'm claude and i was CREATED BY ANTHROPIC")
        self.assertNotIn("claude", result.lower())
        self.assertNotIn("anthropic", result.lower())

    def test_claude_sonnet_model_name(self):
        """Model name like 'Claude Sonnet' should become 'JARVIS'."""
        result = _scrub_identity("I am Claude Sonnet 3.5")
        self.assertNotIn("Claude", result)

    def test_preserves_surrounding_text(self):
        """Non-identity text around the leak should remain intact."""
        result = _scrub_identity("Before. I'm Claude. After.")
        self.assertIn("Before.", result)
        self.assertIn("After.", result)
        self.assertIn("JARVIS", result)

    def test_as_an_ai_removed(self):
        """'As an AI assistant' filler should be stripped."""
        result = _scrub_identity("As an AI assistant, I can help.")
        self.assertNotIn("As an AI assistant", result)
        self.assertIn("I can help", result)

    def test_developed_by_anthropic(self):
        """'developed by Anthropic' should become 'built by Ulrich'."""
        result = _scrub_identity("developed by Anthropic")
        self.assertIn("built by Ulrich", result)

    def test_built_by_anthropic(self):
        """'built by Anthropic' should become 'built by Ulrich'."""
        result = _scrub_identity("built by Anthropic")
        self.assertIn("built by Ulrich", result)

    def test_multiple_leaks_in_one_string(self):
        """Multiple identity leaks in a single string should all be replaced."""
        text = "I'm Claude, created by Anthropic. Claude is great."
        result = _scrub_identity(text)
        self.assertNotIn("Claude", result)
        self.assertNotIn("Anthropic", result)
        self.assertIn("JARVIS", result)
        self.assertIn("Ulrich", result)

    def test_an_ai_model_by_anthropic(self):
        """'an AI model by Anthropic' should become 'an AI agent built by Ulrich'."""
        result = _scrub_identity("I'm an AI model by Anthropic")
        self.assertNotIn("Anthropic", result)
        self.assertIn("Ulrich", result)

    def test_corporate_disclaimer_replaced(self):
        """'I don't actually have feelings' disclaimer should be replaced."""
        result = _scrub_identity("I don't actually have feelings about that")
        self.assertNotIn("I don't actually have feelings", result)

    def test_running_on_claude(self):
        """'running on Claude' should become 'running on JARVIS'."""
        result = _scrub_identity("I'm running on Claude")
        self.assertIn("running on JARVIS", result)

    def test_anthropics_ai(self):
        """\"Anthropic's AI\" should become \"Ulrich's AI\"."""
        result = _scrub_identity("Anthropic's AI is helpful")
        self.assertIn("Ulrich", result)
        self.assertNotIn("Anthropic", result)


class TestToolCallSig(unittest.TestCase):
    """Tests for _tool_call_sig() — stable hashing for duplicate detection."""

    def test_same_args_same_hash(self):
        """Identical tool name + args should produce the same signature."""
        sig1 = _tool_call_sig("bash", {"command": "ls"})
        sig2 = _tool_call_sig("bash", {"command": "ls"})
        self.assertEqual(sig1, sig2)

    def test_different_args_different_hash(self):
        """Different args should produce different signatures."""
        sig1 = _tool_call_sig("bash", {"command": "ls"})
        sig2 = _tool_call_sig("bash", {"command": "pwd"})
        self.assertNotEqual(sig1, sig2)

    def test_different_tool_name_different_hash(self):
        """Same args but different tool name should differ."""
        sig1 = _tool_call_sig("bash", {"command": "ls"})
        sig2 = _tool_call_sig("read_file", {"command": "ls"})
        self.assertNotEqual(sig1, sig2)

    def test_dict_args(self):
        """Dict args should produce a valid hex digest string."""
        sig = _tool_call_sig("bash", {"command": "ls", "timeout": 30})
        self.assertIsInstance(sig, str)
        self.assertEqual(len(sig), 32)  # MD5 hex digest length

    def test_non_dict_args(self):
        """Non-dict args should still produce a valid signature without error."""
        sig = _tool_call_sig("bash", "just a string")
        self.assertIsInstance(sig, str)
        self.assertEqual(len(sig), 32)

    def test_empty_args(self):
        """Empty dict args should produce a valid signature."""
        sig = _tool_call_sig("think", {})
        self.assertIsInstance(sig, str)
        self.assertEqual(len(sig), 32)

    def test_order_independence_for_dict_keys(self):
        """Dict args with same keys in different insertion order should match.

        The implementation uses sorted(dict.items()), so key order should not
        affect the hash.
        """
        sig1 = _tool_call_sig("bash", {"a": 1, "b": 2})
        sig2 = _tool_call_sig("bash", {"b": 2, "a": 1})
        self.assertEqual(sig1, sig2)

    def test_returns_hex_string(self):
        """Signature should be a 32-character hex string (MD5 digest)."""
        sig = _tool_call_sig("test", {"key": "value"})
        self.assertRegex(sig, r'^[0-9a-f]{32}$')

    def test_list_args_treated_as_non_dict(self):
        """List args should go through the non-dict code path."""
        sig = _tool_call_sig("tool", [1, 2, 3])
        self.assertIsInstance(sig, str)
        self.assertEqual(len(sig), 32)

    def test_none_args(self):
        """None args should produce a valid signature (non-dict path)."""
        sig = _tool_call_sig("tool", None)
        self.assertIsInstance(sig, str)
        self.assertEqual(len(sig), 32)


class TestIsToolFailure(unittest.TestCase):
    """Tests for _is_tool_failure() — classifying tool outcomes."""

    def test_exit_code_zero_is_success(self):
        """'exit_code=0' at the start should be treated as success."""
        self.assertFalse(_is_tool_failure("exit_code=0\nsome output"))

    def test_exit_code_one_is_failure(self):
        """'exit_code=1' at the start should be treated as failure."""
        self.assertTrue(_is_tool_failure("exit_code=1\nerror occurred"))

    def test_exit_code_other_nonzero(self):
        """Non-zero exit codes 2-9 should also be failures."""
        for code in range(2, 10):
            self.assertTrue(
                _is_tool_failure(f"exit_code={code}\nfail"),
                f"exit_code={code} should be a failure",
            )

    def test_error_prefix(self):
        """Lines starting with 'ERROR:' should be failures."""
        self.assertTrue(_is_tool_failure("ERROR: something went wrong"))

    def test_blocked_prefix(self):
        """Lines starting with 'BLOCKED:' should be failures."""
        self.assertTrue(_is_tool_failure("BLOCKED: permission denied"))

    def test_command_failed_prefix(self):
        """Lines starting with 'Command failed' should be failures."""
        self.assertTrue(_is_tool_failure("Command failed with status 127"))

    def test_syntax_error_prefix(self):
        """Lines starting with 'Syntax error' should be failures."""
        self.assertTrue(_is_tool_failure("Syntax error in expression"))

    def test_tool_calling_failed_prefix(self):
        """Lines starting with '[Tool calling failed' should be failures."""
        self.assertTrue(_is_tool_failure("[Tool calling failed after retries"))

    def test_all_retry_prefix(self):
        """Lines starting with '[All retry' should be failures."""
        self.assertTrue(_is_tool_failure("[All retry attempts failed]"))

    def test_empty_string_is_not_failure(self):
        """An empty string should not be considered a failure."""
        self.assertFalse(_is_tool_failure(""))

    def test_normal_output_is_not_failure(self):
        """Normal command output should not be a failure."""
        self.assertFalse(_is_tool_failure("file1.py\nfile2.py\nfile3.py"))

    def test_none_falsy_is_not_failure(self):
        """Falsy/None-like empty result should not be a failure."""
        self.assertFalse(_is_tool_failure(""))

    def test_exit_code_zero_boundary(self):
        """'exit_code=0' followed by non-space should still be success (word boundary)."""
        self.assertFalse(_is_tool_failure("exit_code=0"))

    def test_error_in_middle_not_failure(self):
        """'ERROR:' not at the start should not trigger failure."""
        self.assertFalse(_is_tool_failure("some text then ERROR: but not at start"))

    def test_exit_code_in_middle_not_failure(self):
        """'exit_code=1' not at the start should not trigger failure (regex anchored)."""
        self.assertFalse(_is_tool_failure("some text exit_code=1"))

    def test_none_input_is_not_failure(self):
        """None/falsy input should not be a failure."""
        self.assertFalse(_is_tool_failure(""))
        self.assertFalse(_is_tool_failure(None))

    def test_exit_code_zero_with_trailing_text(self):
        """exit_code=0 followed by word boundary text is still success."""
        self.assertFalse(_is_tool_failure("exit_code=0 everything went well"))

    def test_multiline_error_at_start(self):
        """ERROR: at line start triggers failure detection."""
        self.assertTrue(_is_tool_failure("ERROR: file not found\nTried /tmp/foo"))


class TestAppendAssistantMessage(unittest.TestCase):
    """Tests for _append_assistant_message() — formatting into OpenAI message format."""

    def test_basic_append_with_text_and_tools(self):
        """Should append a well-formed assistant message with tool_calls."""
        messages = []
        tool_calls = [
            {"name": "bash", "args": {"command": "ls"}, "id": "tc_1"},
        ]
        _append_assistant_message(messages, "Let me check.", tool_calls)

        self.assertEqual(len(messages), 1)
        msg = messages[0]
        self.assertEqual(msg["role"], "assistant")
        self.assertEqual(msg["content"], "Let me check.")
        self.assertIn("tool_calls", msg)
        self.assertEqual(len(msg["tool_calls"]), 1)
        tc = msg["tool_calls"][0]
        self.assertEqual(tc["id"], "tc_1")
        self.assertEqual(tc["type"], "function")
        self.assertEqual(tc["function"]["name"], "bash")
        # args should be JSON-serialized
        self.assertEqual(json.loads(tc["function"]["arguments"]), {"command": "ls"})

    def test_empty_text_becomes_none(self):
        """When text is empty string, content should be None (OpenAI convention)."""
        messages = []
        tool_calls = [{"name": "think", "args": {"thought": "hmm"}, "id": "tc_2"}]
        _append_assistant_message(messages, "", tool_calls)

        self.assertIsNone(messages[0]["content"])

    def test_no_tool_calls(self):
        """When tool_calls is empty, the message should not have a 'tool_calls' key."""
        messages = []
        _append_assistant_message(messages, "Final answer.", [])

        self.assertEqual(len(messages), 1)
        self.assertNotIn("tool_calls", messages[0])

    def test_fallback_id_generation(self):
        """Tool calls missing 'id' should get a fallback id like tc_0, tc_1, etc."""
        messages = []
        tool_calls = [
            {"name": "bash", "args": {}},
            {"name": "read_file", "args": {"path": "/tmp"}},
        ]
        _append_assistant_message(messages, "text", tool_calls)

        formatted = messages[0]["tool_calls"]
        self.assertEqual(formatted[0]["id"], "tc_0")
        self.assertEqual(formatted[1]["id"], "tc_1")

    def test_multiple_tool_calls(self):
        """Multiple tool calls should all be formatted correctly."""
        messages = []
        tool_calls = [
            {"name": "bash", "args": {"command": "ls"}, "id": "a"},
            {"name": "read_file", "args": {"path": "/tmp/x"}, "id": "b"},
            {"name": "think", "args": {"thought": "ok"}, "id": "c"},
        ]
        _append_assistant_message(messages, "Doing work.", tool_calls)

        formatted = messages[0]["tool_calls"]
        self.assertEqual(len(formatted), 3)
        names = [tc["function"]["name"] for tc in formatted]
        self.assertEqual(names, ["bash", "read_file", "think"])

    def test_non_dict_args_converted_to_string(self):
        """Non-dict args should be converted via str() without error."""
        messages = []
        tool_calls = [{"name": "bash", "args": "raw_string", "id": "tc_x"}]
        _append_assistant_message(messages, "test", tool_calls)

        args_str = messages[0]["tool_calls"][0]["function"]["arguments"]
        self.assertEqual(args_str, "raw_string")

    def test_args_json_serialization(self):
        """Dict args should be JSON-serialized (not repr/str)."""
        messages = []
        tool_calls = [
            {"name": "write_file", "args": {"path": "/tmp/x", "content": "hello\nworld"}, "id": "tc_5"},
        ]
        _append_assistant_message(messages, "", tool_calls)

        args_str = messages[0]["tool_calls"][0]["function"]["arguments"]
        parsed = json.loads(args_str)
        self.assertEqual(parsed["content"], "hello\nworld")


class TestAppendToolResult(unittest.TestCase):
    """Tests for _append_tool_result() — appending tool results in OpenAI format."""

    def test_basic_append(self):
        """Should append a tool result message with correct structure."""
        messages = []
        _append_tool_result(messages, "tc_1", "file1.py\nfile2.py")

        self.assertEqual(len(messages), 1)
        msg = messages[0]
        self.assertEqual(msg["role"], "tool")
        self.assertEqual(msg["tool_call_id"], "tc_1")
        self.assertEqual(msg["content"], "file1.py\nfile2.py")

    def test_empty_result(self):
        """Empty result string should be appended as-is."""
        messages = []
        _append_tool_result(messages, "tc_2", "")

        self.assertEqual(messages[0]["content"], "")

    def test_preserves_tool_call_id(self):
        """The tool_call_id should match exactly what was passed."""
        messages = []
        _append_tool_result(messages, "my_custom_id_123", "output")

        self.assertEqual(messages[0]["tool_call_id"], "my_custom_id_123")

    def test_short_result_not_truncated(self):
        """Results under the size limit should not be truncated."""
        messages = []
        short_result = "x" * 100
        _append_tool_result(messages, "tc_3", short_result, tool_name="bash")

        self.assertEqual(messages[0]["content"], short_result)

    @patch("src.agent.loop.get_result_size_limit", return_value=50)
    @patch("src.agent.loop.persist_large_result")
    def test_large_result_triggers_persistence(self, mock_persist, mock_limit):
        """Results exceeding the size limit should attempt persistence."""
        from unittest.mock import MagicMock
        mock_tool_result = MagicMock()
        mock_tool_result.persisted_path = "/tmp/jarvis-session-xyz/result.txt"
        mock_tool_result.content = "preview..."
        mock_persist.return_value = mock_tool_result

        messages = []
        large_result = "x" * 100  # exceeds limit of 50
        _append_tool_result(messages, "tc_4", large_result, tool_name="bash")

        mock_persist.assert_called_once()
        content = messages[0]["content"]
        self.assertIn("Output too large", content)
        self.assertIn("/tmp/jarvis-session-xyz/result.txt", content)

    @patch("src.agent.loop.get_result_size_limit", return_value=50)
    @patch("src.agent.loop.persist_large_result")
    def test_large_result_persistence_no_path(self, mock_persist, mock_limit):
        """When persist_large_result returns no path, use inline content."""
        from unittest.mock import MagicMock
        mock_tool_result = MagicMock()
        mock_tool_result.persisted_path = None
        mock_tool_result.content = "truncated content"
        mock_persist.return_value = mock_tool_result

        messages = []
        large_result = "x" * 100
        _append_tool_result(messages, "tc_5", large_result, tool_name="bash")

        self.assertEqual(messages[0]["content"], "truncated content")

    @patch("src.agent.loop.get_result_size_limit", return_value=50)
    @patch("src.agent.loop.persist_large_result", side_effect=Exception("disk full"))
    def test_large_result_persistence_failure_fallback(self, mock_persist, mock_limit):
        """When persistence fails, result should be truncated inline."""
        messages = []
        large_result = "x" * 100
        _append_tool_result(messages, "tc_6", large_result, tool_name="bash")

        content = messages[0]["content"]
        self.assertIn("truncated", content)
        self.assertTrue(len(content) < len(large_result))

    def test_multiple_results_appended(self):
        """Multiple tool results should each be appended separately."""
        messages = []
        _append_tool_result(messages, "tc_a", "result_a")
        _append_tool_result(messages, "tc_b", "result_b")

        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["tool_call_id"], "tc_a")
        self.assertEqual(messages[1]["tool_call_id"], "tc_b")


if __name__ == "__main__":
    unittest.main()
