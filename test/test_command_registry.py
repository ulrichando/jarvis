"""Tests for the JARVIS command registry and dispatch system."""

import unittest
import asyncio


class TestCommandRegistry(unittest.TestCase):
    """Test command registration and resolution."""

    def setUp(self):
        from brain.commands import registry
        self.registry = registry

    def test_total_commands(self):
        """100+ commands registered."""
        self.assertGreaterEqual(self.registry.count, 100)

    def test_visible_commands(self):
        """100+ visible commands."""
        self.assertGreaterEqual(self.registry.visible_count, 100)

    def test_hidden_commands(self):
        """Hidden commands exist."""
        all_cmds = self.registry.list_commands(include_hidden=True)
        hidden = [c for c in all_cmds if c.hidden]
        self.assertGreaterEqual(len(hidden), 6)

    def test_resolve_by_name(self):
        cmd = self.registry.resolve("help")
        self.assertIsNotNone(cmd)
        self.assertEqual(cmd.name, "help")

    def test_resolve_by_alias(self):
        cmd = self.registry.resolve("h")
        self.assertIsNotNone(cmd)
        self.assertEqual(cmd.name, "help")

    def test_resolve_with_slash(self):
        cmd = self.registry.resolve("/status")
        self.assertIsNotNone(cmd)
        self.assertEqual(cmd.name, "status")

    def test_resolve_unknown(self):
        cmd = self.registry.resolve("nonexistent_xyz")
        self.assertIsNone(cmd)

    def test_categories(self):
        cats = self.registry.categories()
        self.assertEqual(len(cats), 9)
        cat_slugs = [c[0] for c in cats]
        self.assertIn("core", cat_slugs)
        self.assertIn("memory", cat_slugs)
        self.assertIn("agent", cat_slugs)
        self.assertIn("mcp", cat_slugs)

    def test_list_by_category(self):
        core_cmds = self.registry.list_commands(category="core")
        self.assertGreaterEqual(len(core_cmds), 11)

        agent_cmds = self.registry.list_commands(category="agent")
        self.assertGreaterEqual(len(agent_cmds), 12)

        security_cmds = self.registry.list_commands(category="security")
        self.assertGreaterEqual(len(security_cmds), 6)

    def test_category_counts(self):
        """Verify each category has minimum expected command count."""
        minimums = {
            "core": 11, "session": 9, "memory": 10, "agent": 12,
            "task": 10, "mcp": 10, "plugin": 7, "git": 9, "security": 6,
        }
        for cat, min_count in minimums.items():
            cmds = self.registry.list_commands(category=cat)
            self.assertGreaterEqual(len(cmds), min_count, f"Category '{cat}' expected >= {min_count}, got {len(cmds)}")

    def test_get_help(self):
        help_text = self.registry.get_help("help")
        self.assertIn("/help", help_text)
        self.assertIn("Usage:", help_text)
        self.assertIn("Permission:", help_text)

    def test_get_help_unknown(self):
        help_text = self.registry.get_help("nonexistent")
        self.assertIn("Unknown", help_text)


class TestCommandDispatch(unittest.TestCase):
    """Test async command dispatching."""

    def test_dispatch_help(self):
        from brain.commands import registry, CommandContext
        ctx = CommandContext(args="")
        result = asyncio.get_event_loop().run_until_complete(
            registry.dispatch("help", ctx)
        )
        self.assertIsNotNone(result)
        self.assertTrue(result.success)
        self.assertIn("JARVIS Commands", result.text)
        self.assertIn("commands available", result.text)

    def test_dispatch_version(self):
        from brain.commands import registry, CommandContext
        ctx = CommandContext(args="")
        result = asyncio.get_event_loop().run_until_complete(
            registry.dispatch("version", ctx)
        )
        self.assertIsNotNone(result)
        self.assertIn("JARVIS", result.text)
        self.assertIn("Python", result.text)

    def test_dispatch_unknown(self):
        from brain.commands import registry, CommandContext
        ctx = CommandContext(args="")
        result = asyncio.get_event_loop().run_until_complete(
            registry.dispatch("nonexistent_command", ctx)
        )
        self.assertIsNone(result)

    def test_dispatch_clear_action(self):
        from brain.commands import registry, CommandContext
        ctx = CommandContext(args="")
        result = asyncio.get_event_loop().run_until_complete(
            registry.dispatch("clear", ctx)
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.action, "clear")

    def test_dispatch_exit_action(self):
        from brain.commands import registry, CommandContext
        ctx = CommandContext(args="")
        result = asyncio.get_event_loop().run_until_complete(
            registry.dispatch("exit", ctx)
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.action, "exit")

    def test_dispatch_help_all(self):
        from brain.commands import registry, CommandContext
        ctx = CommandContext(args="--all")
        result = asyncio.get_event_loop().run_until_complete(
            registry.dispatch("help", ctx)
        )
        self.assertIsNotNone(result)
        # Should include hidden debug section
        self.assertIn("Debug", result.text)


class TestCommandAliases(unittest.TestCase):
    """Test that all important aliases resolve correctly."""

    def setUp(self):
        from brain.commands import registry
        self.registry = registry

    def test_alias_pairs(self):
        pairs = [
            ("h", "help"), ("?", "help"),
            ("stat", "status"), ("ver", "version"),
            ("usage", "cost"), ("m", "model"),
            ("perms", "permissions"), ("cfg", "config"),
            ("cls", "clear"), ("quit", "exit"), ("q", "exit"),
            ("sess", "session"), ("c", "resume"), ("continue", "resume"),
            ("hist", "history"),
            ("mem", "memory"),
            ("kb", "knowledge"), ("assoc", "associations"),
            ("cs", "common-sense"), ("profile", "user-profile"),
            ("as", "agent-status"), ("ka", "kill-agent"),
            ("bg", "background"),
            ("ts", "troubleshoot"),
            ("pl", "plugins"), ("sk", "skills"), ("market", "marketplace"),
            ("ci", "commit"), ("br", "branch"), ("wt", "worktree"),
            ("rev", "review"), ("bugs", "bughunter"),
            ("ex", "explain"), ("mon", "monitor"),
        ]
        for alias, expected_name in pairs:
            cmd = self.registry.resolve(alias)
            self.assertIsNotNone(cmd, f"Alias '{alias}' did not resolve")
            self.assertEqual(cmd.name, expected_name, f"Alias '{alias}' resolved to '{cmd.name}' not '{expected_name}'")


if __name__ == "__main__":
    unittest.main()
