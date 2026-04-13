"""Tests for JARVIS new modules: SSE, Sandbox, OAuth, PromptBuilder,
Coordinator, BackgroundRunner, and fuzzy suggestions."""

import asyncio
import hashlib
import base64
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.sse import SseParser, SseEvent
from src.sandbox import (
    detect_sandbox_capabilities, build_sandbox_command, execute_sandboxed,
    SandboxConfig, SandboxStatus,
)
from src.oauth import (
    generate_pkce, build_auth_url, OAuthTokenSet, PkceChallenge,
    save_credentials, load_credentials, CREDENTIALS_PATH,
)
from src.prompt_builder import PromptBuilder, MAX_INSTRUCTION_CHARS
from src.agent.coordinator import AgentCoordinator, AgentHandle
from src.tasks_brain.runner import BackgroundRunner
from src.commands.registry import CommandRegistry, CommandDef, PermLevel, _fuzzy_score


# ── Helpers ────────────────────────────────────────────────────────────

def run_async(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


# ══════════════════════════════════════════════════════════════════════
# SSE Parser Tests
# ══════════════════════════════════════════════════════════════════════

class TestSseParser(unittest.TestCase):

    def test_sse_single_event(self):
        """Push a complete SSE frame, verify parsed event."""
        parser = SseParser()
        events = parser.push("event: message\ndata: hello world\n\n")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event, "message")
        self.assertEqual(events[0].data, "hello world")

    def test_sse_chunked(self):
        """Push data in small chunks, verify events arrive after boundary."""
        parser = SseParser()
        # First chunk: incomplete
        events = parser.push("event: delta\nda")
        self.assertEqual(len(events), 0)
        # Second chunk: completes the data line but no boundary yet
        events = parser.push("ta: partial\n")
        self.assertEqual(len(events), 0)
        # Third chunk: empty line = event boundary
        events = parser.push("\n")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event, "delta")
        self.assertEqual(events[0].data, "partial")

    def test_sse_ping_filtered(self):
        """Verify ping events are skipped."""
        parser = SseParser()
        events = parser.push("event: ping\ndata: keepalive\n\n")
        self.assertEqual(len(events), 0)

    def test_sse_done_filtered(self):
        """Verify [DONE] marker is skipped."""
        parser = SseParser()
        events = parser.push("data: [DONE]\n\n")
        self.assertEqual(len(events), 0)

    def test_sse_multiline_data(self):
        """Verify multi-line data fields are joined with newlines."""
        parser = SseParser()
        events = parser.push("data: line one\ndata: line two\ndata: line three\n\n")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].data, "line one\nline two\nline three")

    def test_sse_json_parsing(self):
        """Verify JSON data is parsed into .parsed dict."""
        parser = SseParser()
        payload = json.dumps({"type": "content_block_delta", "index": 0})
        events = parser.push(f"data: {payload}\n\n")
        self.assertEqual(len(events), 1)
        self.assertIsInstance(events[0].parsed, dict)
        self.assertEqual(events[0].parsed["type"], "content_block_delta")
        self.assertEqual(events[0].parsed["index"], 0)


# ══════════════════════════════════════════════════════════════════════
# Sandbox Tests
# ══════════════════════════════════════════════════════════════════════

class TestSandbox(unittest.TestCase):

    def test_detect_sandbox_capabilities(self):
        """Verify detect returns a SandboxStatus."""
        status = detect_sandbox_capabilities()
        self.assertIsInstance(status, SandboxStatus)
        self.assertIsInstance(status.available, bool)
        self.assertIsInstance(status.fallback_reasons, list)

    def test_build_sandbox_command_disabled(self):
        """Verify raw command returned when sandbox is disabled."""
        config = SandboxConfig(enabled=False)
        cmd, env = build_sandbox_command("echo test", config)
        self.assertEqual(cmd, "echo test")
        self.assertEqual(env, {})

    def test_build_sandbox_command_enabled(self):
        """Verify unshare wrapper when sandbox is enabled (logic test)."""
        config = SandboxConfig(enabled=True, namespace_isolation=True)
        cmd, env = build_sandbox_command("whoami", config, cwd="/tmp")
        status = detect_sandbox_capabilities()
        if status.available:
            # Should contain unshare flags
            self.assertIn("unshare", cmd)
            self.assertIn("--user", cmd)
            self.assertIn("--fork", cmd)
            self.assertIn("whoami", cmd)
            # Extra env should set sandbox markers
            self.assertEqual(env.get("JARVIS_SANDBOX"), "1")
        else:
            # Falls back to raw command on systems without unshare
            self.assertEqual(cmd, "whoami")

    def test_execute_sandboxed_simple(self):
        """Run echo hello and verify output."""
        config = SandboxConfig(enabled=False)  # Disable sandbox for portability
        result = execute_sandboxed("echo hello", config=config)
        self.assertEqual(result["returncode"], 0)
        self.assertIn("hello", result["stdout"])


# ══════════════════════════════════════════════════════════════════════
# OAuth Tests
# ══════════════════════════════════════════════════════════════════════

class TestOAuth(unittest.TestCase):

    def test_generate_pkce(self):
        """Verify verifier and challenge are generated, challenge is SHA256."""
        pkce = generate_pkce()
        self.assertTrue(len(pkce.verifier) > 0)
        self.assertTrue(len(pkce.challenge) > 0)
        self.assertEqual(pkce.method, "S256")
        # Verify challenge is SHA256 of verifier
        expected = base64.urlsafe_b64encode(
            hashlib.sha256(pkce.verifier.encode()).digest()
        ).rstrip(b"=").decode()
        self.assertEqual(pkce.challenge, expected)

    def test_build_auth_url(self):
        """Verify URL contains all required params."""
        pkce = generate_pkce()
        url = build_auth_url(
            authorize_url="https://auth.example.com/authorize",
            client_id="my-client",
            redirect_uri="http://localhost:8080/callback",
            scopes=["read", "write"],
            state="random-state",
            pkce=pkce,
        )
        self.assertIn("https://auth.example.com/authorize?", url)
        self.assertIn("response_type=code", url)
        self.assertIn("client_id=my-client", url)
        self.assertIn("redirect_uri=", url)
        self.assertIn("scope=read+write", url)
        self.assertIn("state=random-state", url)
        self.assertIn("code_challenge=", url)
        self.assertIn("code_challenge_method=S256", url)

    def test_token_expired(self):
        """Verify is_expired works."""
        # Expired token
        expired = OAuthTokenSet(access_token="tok", expires_at=int(time.time()) - 100)
        self.assertTrue(expired.is_expired)
        # Valid token
        valid = OAuthTokenSet(access_token="tok", expires_at=int(time.time()) + 3600)
        self.assertFalse(valid.is_expired)
        # No expiry set
        no_expiry = OAuthTokenSet(access_token="tok", expires_at=0)
        self.assertFalse(no_expiry.is_expired)

    def test_save_load_credentials(self):
        """Save and load back credentials using temp dir."""
        with tempfile.TemporaryDirectory() as tmpdir:
            creds_path = Path(tmpdir) / "oauth_credentials.json"
            token_set = OAuthTokenSet(
                access_token="test-access-token",
                refresh_token="test-refresh-token",
                expires_at=int(time.time()) + 3600,
                scopes=["read", "write"],
            )
            # Patch CREDENTIALS_PATH and JARVIS_HOME to use temp dir
            with patch("src.oauth.CREDENTIALS_PATH", creds_path), \
                 patch("src.oauth.JARVIS_HOME", Path(tmpdir)):
                save_credentials("test-provider", token_set)
                loaded = load_credentials("test-provider")

            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.access_token, "test-access-token")
            self.assertEqual(loaded.refresh_token, "test-refresh-token")
            self.assertEqual(loaded.scopes, ["read", "write"])


# ══════════════════════════════════════════════════════════════════════
# Prompt Builder Tests
# ══════════════════════════════════════════════════════════════════════

class TestPromptBuilder(unittest.TestCase):

    def test_discover_stack(self):
        """Create temp dir with Cargo.toml, verify Rust detected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "Cargo.toml").write_text("[package]\nname = \"test\"")
            builder = PromptBuilder(cwd=tmpdir)
            stack = builder._detect_stack()
            self.assertIn("Rust", stack)

    def test_discover_instructions(self):
        """Create temp dir with JARVIS.md, verify discovered."""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "JARVIS.md").write_text("# Project Instructions\nDo the thing.")
            builder = PromptBuilder(cwd=tmpdir)
            instructions = builder._discover_instructions()
            found = [i for i in instructions if i.path.name == "JARVIS.md"]
            self.assertTrue(len(found) > 0)
            self.assertIn("Do the thing", found[0].content)

    def test_build_prompt(self):
        """Verify base prompt is in output, date is in output."""
        with tempfile.TemporaryDirectory() as tmpdir:
            builder = PromptBuilder(cwd=tmpdir)
            ctx = builder.discover_context()
            result = builder.build("You are JARVIS.", ctx)
            self.assertIn("You are JARVIS.", result)
            self.assertIn("Date:", result)

    def test_instruction_cap(self):
        """Verify files over MAX_INSTRUCTION_CHARS are truncated."""
        with tempfile.TemporaryDirectory() as tmpdir:
            huge = "A" * (MAX_INSTRUCTION_CHARS + 500)
            (Path(tmpdir) / "JARVIS.md").write_text(huge)
            builder = PromptBuilder(cwd=tmpdir)
            ctx = builder.discover_context()
            result = builder.build("Base.", ctx)
            self.assertIn("truncated", result)
            # The full huge string should NOT be in the output
            self.assertNotIn(huge, result)


# ══════════════════════════════════════════════════════════════════════
# Coordinator Tests
# ══════════════════════════════════════════════════════════════════════

class TestCoordinator(unittest.TestCase):

    def test_list_running_empty(self):
        """Verify empty list initially."""
        coord = AgentCoordinator()
        self.assertEqual(coord.list_running(), [])
        self.assertEqual(coord.list_all(), [])

    def test_cleanup(self):
        """Verify old entries are removed."""
        coord = AgentCoordinator()
        # Manually insert a completed agent with old timestamp
        handle = AgentHandle(
            id="test123",
            agent_type="worker",
            task="do something",
            status="done",
            created_at=time.time() - 7200,  # 2 hours ago
        )
        coord._agents["test123"] = handle
        self.assertEqual(len(coord._agents), 1)
        coord.cleanup(max_age=3600)
        self.assertEqual(len(coord._agents), 0)


# ══════════════════════════════════════════════════════════════════════
# Background Runner Tests
# ══════════════════════════════════════════════════════════════════════

class TestBackgroundRunner(unittest.TestCase):

    def test_run_and_complete(self):
        """Run a coroutine, wait, verify done."""
        runner = BackgroundRunner()

        async def do_work():
            await asyncio.sleep(0.05)
            return "finished"

        async def main():
            task_id = runner.run("test-task", do_work())
            # Wait for completion
            await asyncio.sleep(0.2)
            status = runner.status(task_id)
            self.assertIsNotNone(status)
            self.assertEqual(status["status"], "done")
            self.assertIn("finished", status["result"])

        asyncio.run(main())

    def test_cancel(self):
        """Run a long task, cancel it, verify cancelled."""
        runner = BackgroundRunner()

        async def long_work():
            await asyncio.sleep(60)

        async def main():
            task_id = runner.run("long-task", long_work())
            await asyncio.sleep(0.05)
            cancelled = runner.cancel(task_id)
            self.assertTrue(cancelled)
            await asyncio.sleep(0.05)
            status = runner.status(task_id)
            self.assertEqual(status["status"], "cancelled")

        asyncio.run(main())

    def test_list_running(self):
        """Verify running tasks appear in list."""
        runner = BackgroundRunner()

        async def slow():
            await asyncio.sleep(10)

        async def main():
            runner.run("task-a", slow())
            runner.run("task-b", slow())
            await asyncio.sleep(0.05)
            running = runner.list_running()
            names = [t["name"] for t in running]
            self.assertIn("task-a", names)
            self.assertIn("task-b", names)
            # Cleanup
            for t in runner._tasks.values():
                if t._task:
                    t._task.cancel()
            await asyncio.sleep(0.05)

        asyncio.run(main())


# ══════════════════════════════════════════════════════════════════════
# Fuzzy Suggestions Tests
# ══════════════════════════════════════════════════════════════════════

class TestFuzzySuggestions(unittest.TestCase):

    def _make_registry(self):
        """Create a registry with some test commands."""
        reg = CommandRegistry()
        reg.register(CommandDef(
            name="help", aliases=["/h", "/?"], description="Show help",
            usage="/help", category="general", permission=PermLevel.READ_ONLY,
            handler=lambda ctx: None, hidden=False,
        ))
        reg.register(CommandDef(
            name="history", aliases=["/hist"], description="Show history",
            usage="/history", category="general", permission=PermLevel.READ_ONLY,
            handler=lambda ctx: None, hidden=False,
        ))
        reg.register(CommandDef(
            name="scan", aliases=[], description="Scan system",
            usage="/scan", category="security", permission=PermLevel.STANDARD,
            handler=lambda ctx: None, hidden=False,
        ))
        return reg

    def test_suggest_exact(self):
        """Verify exact match scores highest."""
        reg = self._make_registry()
        results = reg.suggest("help")
        self.assertTrue(len(results) > 0)
        self.assertEqual(results[0].name, "help")

    def test_suggest_prefix(self):
        """Verify prefix match works."""
        reg = self._make_registry()
        results = reg.suggest("hel")
        self.assertTrue(len(results) > 0)
        # 'help' should be in results (prefix match)
        names = [r.name for r in results]
        self.assertIn("help", names)

    def test_suggest_empty(self):
        """Verify empty query returns empty."""
        reg = self._make_registry()
        results = reg.suggest("")
        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()
