"""E2E tests for Brain-level routing using mocks — no real DB or LLM required."""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch, AsyncMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))


class TestBrainRouting(unittest.TestCase):
    """Test Brain's _needs_agent_loop routing logic in isolation."""

    def _make_brain(self, mode: str = "normal"):
        """Patch all heavy Brain dependencies and return a Brain instance."""
        patches = [
            patch("src.brain.MemoryStore", MagicMock),
            patch("src.brain.GroqReasoner", MagicMock),
            patch("src.brain.HooksManager", MagicMock),
            patch("src.brain.CheckpointManager", MagicMock, create=True),
            patch("src.brain.PermissionManager", MagicMock, create=True),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

        from src.brain import Brain
        with patch.object(Brain, "__init__", lambda self, **kw: None):
            brain = Brain.__new__(Brain)
            # Set minimal required attributes
            brain.mode = mode
            brain.reasoner = MagicMock()
            brain.memory = MagicMock()
            brain.hooks = MagicMock()
        return brain

    def test_needs_agent_loop_for_file_tasks(self):
        """File creation tasks should route to the agent loop."""
        brain = self._make_brain(mode="normal")
        result = brain._needs_agent_loop("create a file called test.py")
        self.assertTrue(result, "File creation tasks should use the agent loop")

    def test_needs_agent_loop_false_for_greetings(self):
        """Exact conversational phrases should not require the agent loop (mode=normal)."""
        # In "agent" mode _needs_agent_loop always returns True.
        # Use "normal" mode to test the conversational classifier.
        # The classifier uses exact set membership — "how are you" is in the set.
        brain = self._make_brain(mode="normal")
        result = brain._needs_agent_loop("how are you")
        self.assertFalse(result, "'how are you' is a conversational phrase — no agent loop needed")

    def test_mode_switching(self):
        """Brain.mode should be writable."""
        brain = self._make_brain(mode="normal")
        brain.mode = "plan"
        self.assertEqual(brain.mode, "plan")
        brain.mode = "agent"
        self.assertEqual(brain.mode, "agent")

    def test_needs_agent_loop_for_bash_tasks(self):
        """Tasks that request bash/shell operations should use the agent loop."""
        brain = self._make_brain(mode="normal")
        result = brain._needs_agent_loop("run this bash command: ls -la")
        self.assertTrue(result, "Bash tasks should use the agent loop")

    def test_needs_agent_loop_for_web_search(self):
        """Web search tasks should use the agent loop."""
        brain = self._make_brain(mode="normal")
        result = brain._needs_agent_loop("search the web for Python tutorials")
        self.assertTrue(result, "Web search should use the agent loop")
