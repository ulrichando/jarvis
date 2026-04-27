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
