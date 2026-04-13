"""Tests for commands_registry availability and skill filtering.

Covers:
- meets_availability_requirement()
- get_slash_command_tool_skills()
"""

import asyncio
import unittest
from unittest.mock import patch, AsyncMock

from src.commands_registry import (
    Command,
    CommandAvailability,
    CommandType,
    meets_availability_requirement,
    get_slash_command_tool_skills,
    _load_all_commands_cache,
)


def _make_command(
    name: str = "test_cmd",
    cmd_type: CommandType = CommandType.PROMPT,
    source: str = "skills",
    loaded_from: str | None = "skills",
    availability: list[CommandAvailability] | None = None,
    disable_model_invocation: bool = False,
    has_user_specified_description: bool = True,
    when_to_use: str | None = None,
) -> Command:
    """Convenience factory for building Command objects for tests."""
    return Command(
        type=cmd_type,
        name=name,
        description=f"Test command: {name}",
        source=source,
        loaded_from=loaded_from,
        availability=availability,
        disable_model_invocation=disable_model_invocation,
        has_user_specified_description=has_user_specified_description,
        when_to_use=when_to_use,
    )


# ---------------------------------------------------------------------------
# meets_availability_requirement
# ---------------------------------------------------------------------------


class TestMeetsAvailabilityRequirement(unittest.TestCase):
    """Validate the availability gate on commands."""

    def test_no_availability_returns_true(self):
        """Commands with availability=None are universally available."""
        cmd = _make_command(availability=None)
        self.assertTrue(meets_availability_requirement(cmd))

    def test_empty_availability_returns_true(self):
        """Commands with an empty availability list should be available."""
        cmd = _make_command(availability=[])
        self.assertTrue(meets_availability_requirement(cmd))

    def test_claude_ai_returns_false(self):
        """CLAUDE_AI availability should return False (auth not implemented)."""
        cmd = _make_command(availability=[CommandAvailability.CLAUDE_AI])
        self.assertFalse(meets_availability_requirement(cmd))

    def test_console_returns_false(self):
        """CONSOLE availability should return False (auth not implemented)."""
        cmd = _make_command(availability=[CommandAvailability.CONSOLE])
        self.assertFalse(meets_availability_requirement(cmd))

    def test_both_claude_and_console_returns_false(self):
        """If both CLAUDE_AI and CONSOLE are listed, should still be False.

        The function iterates and the first match (CLAUDE_AI) returns False.
        """
        cmd = _make_command(
            availability=[CommandAvailability.CLAUDE_AI, CommandAvailability.CONSOLE]
        )
        self.assertFalse(meets_availability_requirement(cmd))

    def test_console_only_returns_false(self):
        """Single CONSOLE in list -> False."""
        cmd = _make_command(availability=[CommandAvailability.CONSOLE])
        self.assertFalse(meets_availability_requirement(cmd))


# ---------------------------------------------------------------------------
# get_slash_command_tool_skills
# ---------------------------------------------------------------------------


class TestGetSlashCommandToolSkills(unittest.TestCase):
    """Validate the filtering logic of get_slash_command_tool_skills."""

    def setUp(self):
        # Clear the memoization cache before each test so our patched
        # _load_all_commands data takes effect.
        _load_all_commands_cache.clear()

    def tearDown(self):
        _load_all_commands_cache.clear()

    def _run(self, coro):
        """Helper to run async code from sync tests."""
        return asyncio.run(coro)

    # -- disable_model_invocation filtering ---------------------------------

    def test_disable_model_invocation_excluded(self):
        """Commands with disable_model_invocation=True must be excluded."""
        included = _make_command(
            name="good_skill",
            loaded_from="skills",
            disable_model_invocation=False,
            has_user_specified_description=True,
        )
        excluded = _make_command(
            name="hidden_skill",
            loaded_from="skills",
            disable_model_invocation=True,
            has_user_specified_description=True,
        )
        with patch(
            "src.commands_registry._load_all_commands",
            new_callable=AsyncMock,
            return_value=[included, excluded],
        ):
            result = self._run(get_slash_command_tool_skills("/tmp"))
        names = [c.name for c in result]
        self.assertIn("good_skill", names)
        self.assertNotIn("hidden_skill", names)

    # -- source / loaded_from filtering -------------------------------------

    def test_only_prompt_type_included(self):
        """Only PROMPT type commands should appear."""
        prompt_cmd = _make_command(name="prompt_cmd", cmd_type=CommandType.PROMPT)
        local_cmd = _make_command(name="local_cmd", cmd_type=CommandType.LOCAL)
        with patch(
            "src.commands_registry._load_all_commands",
            new_callable=AsyncMock,
            return_value=[prompt_cmd, local_cmd],
        ):
            result = self._run(get_slash_command_tool_skills("/tmp"))
        names = [c.name for c in result]
        self.assertIn("prompt_cmd", names)
        self.assertNotIn("local_cmd", names)

    def test_builtin_source_excluded(self):
        """Commands with source='builtin' should be excluded."""
        builtin_cmd = _make_command(name="builtin_cmd", source="builtin")
        skills_cmd = _make_command(name="skills_cmd", source="skills")
        with patch(
            "src.commands_registry._load_all_commands",
            new_callable=AsyncMock,
            return_value=[builtin_cmd, skills_cmd],
        ):
            result = self._run(get_slash_command_tool_skills("/tmp"))
        names = [c.name for c in result]
        self.assertNotIn("builtin_cmd", names)
        self.assertIn("skills_cmd", names)

    def test_valid_loaded_from_values(self):
        """Commands loaded from skills, plugin, or bundled should be included."""
        for source_val in ("skills", "plugin", "bundled"):
            cmd = _make_command(name=f"cmd_{source_val}", loaded_from=source_val)
            with patch(
                "src.commands_registry._load_all_commands",
                new_callable=AsyncMock,
                return_value=[cmd],
            ):
                result = self._run(get_slash_command_tool_skills("/tmp"))
            self.assertEqual(
                len(result), 1,
                f"Command loaded_from='{source_val}' should be included",
            )

    def test_invalid_loaded_from_excluded(self):
        """Commands from unknown loaded_from without description/when_to_use are excluded."""
        cmd = _make_command(
            name="other_cmd",
            loaded_from="other",
            has_user_specified_description=False,
            when_to_use=None,
        )
        with patch(
            "src.commands_registry._load_all_commands",
            new_callable=AsyncMock,
            return_value=[cmd],
        ):
            result = self._run(get_slash_command_tool_skills("/tmp"))
        self.assertEqual(len(result), 0)

    def test_needs_description_or_when_to_use(self):
        """Commands must have has_user_specified_description or when_to_use."""
        cmd_no_desc = _make_command(
            name="no_desc",
            loaded_from="skills",
            has_user_specified_description=False,
            when_to_use=None,
        )
        cmd_with_desc = _make_command(
            name="with_desc",
            loaded_from="skills",
            has_user_specified_description=True,
            when_to_use=None,
        )
        cmd_with_when = _make_command(
            name="with_when",
            loaded_from="skills",
            has_user_specified_description=False,
            when_to_use="Use when doing X",
        )
        with patch(
            "src.commands_registry._load_all_commands",
            new_callable=AsyncMock,
            return_value=[cmd_no_desc, cmd_with_desc, cmd_with_when],
        ):
            result = self._run(get_slash_command_tool_skills("/tmp"))
        names = [c.name for c in result]
        self.assertNotIn("no_desc", names)
        self.assertIn("with_desc", names)
        self.assertIn("with_when", names)

    def test_combined_filter(self):
        """End-to-end: only the right commands survive all filters."""
        commands = [
            # Should be included: PROMPT, skills, not disabled, has desc
            _make_command(name="ok", loaded_from="skills"),
            # Excluded: disabled
            _make_command(name="disabled", loaded_from="skills", disable_model_invocation=True),
            # Excluded: wrong type
            _make_command(name="local", loaded_from="skills", cmd_type=CommandType.LOCAL),
            # Excluded: builtin source
            _make_command(name="builtin", loaded_from="skills", source="builtin"),
            # Included: plugin loaded_from, has when_to_use
            _make_command(
                name="plugin_skill",
                loaded_from="plugin",
                has_user_specified_description=False,
                when_to_use="Use for plugins",
            ),
        ]
        with patch(
            "src.commands_registry._load_all_commands",
            new_callable=AsyncMock,
            return_value=commands,
        ):
            result = self._run(get_slash_command_tool_skills("/tmp"))
        names = [c.name for c in result]
        self.assertEqual(sorted(names), ["ok", "plugin_skill"])


if __name__ == "__main__":
    unittest.main()
