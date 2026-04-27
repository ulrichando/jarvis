"""Tests for the JARVIS silence fix — quiet-hours constants and session watchdog."""
import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

# Add voice-agent dir to path so we can import the module directly
sys.path.insert(0, str(Path(__file__).parent.parent))


class TestQuietHoursConstants:
    """Quiet-hours defaults match the tightened spec values."""

    def test_quiet_hours_start_default(self):
        # Must be 1 (1am) — not 23 (11pm). Tightening removes the 11pm-1am block.
        import importlib
        import jarvis_agent
        importlib.reload(jarvis_agent)
        assert jarvis_agent.QUIET_HOURS_START == 1, (
            f"Expected QUIET_HOURS_START=1, got {jarvis_agent.QUIET_HOURS_START}"
        )

    def test_quiet_hours_end_default(self):
        # Must be 6 (6am) — not 7 (7am). 6am-7am is morning, not sleep.
        import importlib
        import jarvis_agent
        importlib.reload(jarvis_agent)
        assert jarvis_agent.QUIET_HOURS_END == 6, (
            f"Expected QUIET_HOURS_END=6, got {jarvis_agent.QUIET_HOURS_END}"
        )

    def test_quiet_hours_window_default(self):
        # Must be 1200.0 (20 min) — not 300 (5 min). Natural pauses exceed 5 min.
        import importlib
        import jarvis_agent
        importlib.reload(jarvis_agent)
        assert jarvis_agent.QUIET_HOURS_WINDOW_SEC == 1200.0, (
            f"Expected QUIET_HOURS_WINDOW_SEC=1200.0, got {jarvis_agent.QUIET_HOURS_WINDOW_SEC}"
        )

    def test_quiet_hours_window_env_override(self):
        # JARVIS_QUIET_WINDOW_SEC env var must override the default.
        with patch.dict(os.environ, {"JARVIS_QUIET_WINDOW_SEC": "600"}):
            import importlib
            import jarvis_agent
            importlib.reload(jarvis_agent)
            assert jarvis_agent.QUIET_HOURS_WINDOW_SEC == 600.0, (
                f"Expected QUIET_HOURS_WINDOW_SEC=600.0 (from env), got {jarvis_agent.QUIET_HOURS_WINDOW_SEC}"
            )


class TestSessionWatchdog:
    """_restart_voice_client_after_crash calls Popen with the right systemctl command."""

    def test_restart_calls_popen(self):
        import importlib
        import jarvis_agent
        importlib.reload(jarvis_agent)

        with patch("jarvis_agent._subprocess.Popen") as mock_popen, \
             patch("asyncio.sleep", new=AsyncMock()):
            asyncio.run(jarvis_agent._restart_voice_client_after_crash())
            mock_popen.assert_called_once_with(
                ["systemctl", "--user", "restart", "jarvis-voice-client"],
                stdout=jarvis_agent._subprocess.DEVNULL,
                stderr=jarvis_agent._subprocess.DEVNULL,
            )

    def test_restart_is_nonblocking_popen(self):
        """Must use Popen (fire-and-forget), NOT check_call/run which would block."""
        import importlib
        import jarvis_agent
        importlib.reload(jarvis_agent)

        with patch("jarvis_agent._subprocess.Popen") as mock_popen, \
             patch("asyncio.sleep", new=AsyncMock()):
            asyncio.run(jarvis_agent._restart_voice_client_after_crash())
            # Popen called once — not check_call, not run
            assert mock_popen.call_count == 1
