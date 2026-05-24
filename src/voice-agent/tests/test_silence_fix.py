"""Tests for the JARVIS silence fix — quiet-hours constants and session watchdog."""
import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

# Add voice-agent dir to path so we can import the module directly
sys.path.insert(0, str(Path(__file__).parent.parent))


class TestQuietHoursConstants:
    """Quiet-hours defaults: OFF by default per user directive
    2026-05-10 (JARVIS should be active 24/7). Set JARVIS_QUIET_START /
    JARVIS_QUIET_END to re-enable a window."""

    def test_quiet_hours_start_default_off(self):
        # 2026-05-10: defaults flipped to 0 (off) so JARVIS is always
        # listening. Original 1am window can be restored via env vars.
        import importlib
        import jarvis_agent
        importlib.reload(jarvis_agent)
        assert jarvis_agent.QUIET_HOURS_START == 0, (
            f"Expected QUIET_HOURS_START=0 (off), got {jarvis_agent.QUIET_HOURS_START}"
        )

    def test_quiet_hours_end_default_off(self):
        # 2026-05-10: defaults flipped to 0 (off). The `if START == END:
        # return False` short-circuit in _in_quiet_hours() makes this
        # equivalent to "no quiet hours".
        import importlib
        import jarvis_agent
        importlib.reload(jarvis_agent)
        assert jarvis_agent.QUIET_HOURS_END == 0, (
            f"Expected QUIET_HOURS_END=0 (off), got {jarvis_agent.QUIET_HOURS_END}"
        )

    def test_quiet_hours_window_default(self):
        # Window kept at 1200s (20 min) — used only when START != END.
        import importlib
        import jarvis_agent
        importlib.reload(jarvis_agent)
        assert jarvis_agent.QUIET_HOURS_WINDOW_SEC == 1200.0, (
            f"Expected QUIET_HOURS_WINDOW_SEC=1200.0, got {jarvis_agent.QUIET_HOURS_WINDOW_SEC}"
        )

    def test_quiet_hours_disabled_when_start_equals_end(self):
        # The _in_quiet_hours() function returns False when START == END,
        # which is the production default post-2026-05-10.
        import importlib
        import jarvis_agent
        importlib.reload(jarvis_agent)
        assert jarvis_agent._in_quiet_hours() is False, (
            "With START == END == 0, quiet-hours must be permanently OFF"
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
    """_restart_voice_client_after_crash calls service_control.restart_service.

    Phase 2.3 (2026-05-24): the call shape changed from a direct
    `_subprocess.Popen(["systemctl", "--user", "restart", ...])` to
    `pipeline.service_control.restart_service("jarvis-voice-client")`.
    The systemctl argv is now an implementation detail of
    service_control (with its own platform.system() dispatch + tests in
    test_service_control.py); this test asserts only the contract that
    jarvis_agent calls the helper for the right unit name.
    """

    def test_restart_calls_service_control(self):
        import importlib
        import jarvis_agent
        importlib.reload(jarvis_agent)

        with patch("pipeline.service_control.restart_service") as mock_restart, \
             patch("asyncio.sleep", new=AsyncMock()):
            asyncio.run(jarvis_agent._restart_voice_client_after_crash())
            mock_restart.assert_called_once_with("jarvis-voice-client")

    def test_restart_is_nonblocking(self):
        """Must dispatch via service_control.restart_service (which uses
        fire-and-forget Popen on Linux), NOT check_call/run which would block."""
        import importlib
        import jarvis_agent
        importlib.reload(jarvis_agent)

        with patch("pipeline.service_control.restart_service") as mock_restart, \
             patch("asyncio.sleep", new=AsyncMock()):
            asyncio.run(jarvis_agent._restart_voice_client_after_crash())
            # restart_service called exactly once — not check_call, not run
            assert mock_restart.call_count == 1

    def test_no_restart_on_clean_shutdown(self):
        """CloseEvent with error=None must NOT schedule a restart."""
        import importlib
        import jarvis_agent
        importlib.reload(jarvis_agent)

        mock_ev = MagicMock()
        mock_ev.error = None
        assert not jarvis_agent._session_close_needs_restart(mock_ev)

    def test_restart_on_crash_error(self):
        """CloseEvent with a non-None error MUST schedule a restart."""
        import importlib
        import jarvis_agent
        importlib.reload(jarvis_agent)

        mock_ev = MagicMock()
        mock_ev.error = Exception("Connection error")
        assert jarvis_agent._session_close_needs_restart(mock_ev)
