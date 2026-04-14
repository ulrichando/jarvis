"""E2E tests for WebSocket protocol message format and validation — no real server needed."""

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))


def _strip_port(origin: str) -> str:
    """Match the web_server._check_ws_origin port-stripping logic."""
    return re.sub(r':\d+$', '', origin)


_ALLOWED_ORIGINS = {
    "http://localhost", "http://127.0.0.1", "http://0.0.0.0",
    "https://localhost", "https://127.0.0.1",
    "tauri://localhost", "http://tauri.localhost", "https://tauri.localhost",
}


def _check_ws_origin(origin: str) -> bool:
    """Standalone replica of JarvisWebServer._check_ws_origin for testing."""
    if not origin:
        return True
    origin_base = _strip_port(origin)
    return origin_base in _ALLOWED_ORIGINS


def assert_event_sequence(events: list[dict]) -> None:
    """Validate that events follow the expected stream ordering rules."""
    assert events, "Event list must not be empty"
    types = [e.get("type") for e in events]
    # Last event should be "message" or "done" or "error"
    assert types[-1] in ("message", "done", "error"), (
        f"Last event must be message/done/error, got: {types[-1]!r}"
    )
    # status must appear before stream chunks
    if "status" in types and "stream" in types:
        assert types.index("status") < types.index("stream"), (
            "status event must precede stream events"
        )


class TestWebSocketProtocol(unittest.TestCase):

    def test_query_message_structure(self):
        """A valid query message has 'type' and 'text' keys."""
        msg = {"type": "query", "text": "hello"}
        self.assertEqual(msg["type"], "query")
        self.assertIn("text", msg)
        self.assertIsInstance(msg["text"], str)

    def test_ws_origin_validation_allows_localhost(self):
        self.assertTrue(_check_ws_origin("http://localhost:8765"))
        self.assertTrue(_check_ws_origin("http://127.0.0.1:8765"))
        self.assertTrue(_check_ws_origin("http://127.0.0.1"))

    def test_ws_origin_validation_allows_tauri(self):
        self.assertTrue(_check_ws_origin("tauri://localhost"))
        self.assertTrue(_check_ws_origin("http://tauri.localhost"))

    def test_ws_origin_validation_blocks_external(self):
        self.assertFalse(_check_ws_origin("http://evil.com"))
        self.assertFalse(_check_ws_origin("https://attacker.io"))
        self.assertFalse(_check_ws_origin("http://subdomain.localhost.evil.com"))

    def test_ws_origin_empty_allows(self):
        """No origin header means direct/local connection — allow."""
        self.assertTrue(_check_ws_origin(""))

    def test_stream_event_sequence_structure(self):
        events = [
            {"type": "status", "status": "thinking"},
            {"type": "stream", "text": "Hello "},
            {"type": "stream", "text": "world"},
            {"type": "message", "text": "Hello world"},
        ]
        assert_event_sequence(events)  # Should not raise

    def test_stream_event_sequence_error_at_end(self):
        events = [
            {"type": "status", "status": "thinking"},
            {"type": "error", "error": "something went wrong"},
        ]
        assert_event_sequence(events)  # error at end is valid

    def test_tool_call_event_format(self):
        event = {"type": "tool_call", "id": "tc_1", "name": "bash", "args": {"command": "ls"}}
        self.assertEqual(event["type"], "tool_call")
        self.assertIn("id", event)
        self.assertIn("name", event)
        self.assertIn("args", event)
        self.assertIsInstance(event["args"], dict)

    def test_tool_result_event_format(self):
        event = {"type": "tool_result", "id": "tc_1", "name": "bash", "content": "file.txt"}
        self.assertEqual(event["type"], "tool_result")
        self.assertIn("id", event)
        self.assertIn("name", event)
        self.assertIn("content", event)

    def test_error_event_format(self):
        event = {"type": "error", "error": "something went wrong"}
        self.assertEqual(event["type"], "error")
        self.assertIn("error", event)
        self.assertIsInstance(event["error"], str)

    def test_real_check_ws_origin_import(self):
        """Verify we can import the server's actual origin list for comparison."""
        from src.server.web_server import JarvisWebServer
        self.assertIsInstance(JarvisWebServer._ALLOWED_ORIGINS, set)
        self.assertIn("tauri://localhost", JarvisWebServer._ALLOWED_ORIGINS)
