"""
Built-in terminal panel.

Uses tmux for shell persistence on systems where it's available,
falling back to a non-persistent shell otherwise.
"""

import os
import subprocess
from typing import Optional


TMUX_SESSION = "panel"


def get_terminal_panel_socket() -> str:
    """Get the tmux socket name for the terminal panel."""
    session_id = os.environ.get("JARVIS_SESSION_ID", "default")
    return f"claude-panel-{session_id[:8]}"


class TerminalPanel:
    """Terminal panel with tmux support for shell persistence."""

    def __init__(self):
        self._has_tmux: Optional[bool] = None
        self._cleanup_registered = False

    def toggle(self) -> None:
        """Toggle the terminal panel."""
        self._show_shell()

    def _check_tmux(self) -> bool:
        """Check if tmux is available."""
        if self._has_tmux is not None:
            return self._has_tmux

        try:
            result = subprocess.run(
                ["tmux", "-V"],
                capture_output=True,
                text=True,
            )
            self._has_tmux = result.returncode == 0
        except FileNotFoundError:
            self._has_tmux = False

        return self._has_tmux

    def _has_session(self) -> bool:
        """Check if a tmux session exists."""
        result = subprocess.run(
            ["tmux", "-L", get_terminal_panel_socket(), "has-session", "-t", TMUX_SESSION],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    def _create_session(self) -> bool:
        """Create a new tmux session."""
        shell = os.environ.get("SHELL", "/bin/bash")
        cwd = os.getcwd()
        socket = get_terminal_panel_socket()

        result = subprocess.run(
            [
                "tmux", "-L", socket,
                "new-session", "-d", "-s", TMUX_SESSION,
                "-c", cwd, shell, "-l",
            ],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            return False

        # Bind Meta+J and configure status bar
        subprocess.run([
            "tmux", "-L", socket,
            "bind-key", "-n", "M-j", "detach-client", ";",
            "set-option", "-g", "status-right", " Alt+J to return ",
        ])

        return True

    def _attach_session(self) -> None:
        """Attach to existing tmux session."""
        subprocess.run(
            ["tmux", "-L", get_terminal_panel_socket(), "attach-session", "-t", TMUX_SESSION],
        )

    def _ensure_session(self) -> bool:
        """Ensure a tmux session exists, creating one if needed."""
        if self._has_session():
            return True
        return self._create_session()

    def _run_shell_direct(self) -> None:
        """Fallback when tmux is not available."""
        shell = os.environ.get("SHELL", "/bin/bash")
        subprocess.run([shell, "-i", "-l"], cwd=os.getcwd())

    def _show_shell(self) -> None:
        """Show the terminal shell."""
        if self._check_tmux() and self._ensure_session():
            self._attach_session()
        else:
            self._run_shell_direct()


_instance: Optional[TerminalPanel] = None


def get_terminal_panel() -> TerminalPanel:
    """Return the singleton TerminalPanel."""
    global _instance
    if _instance is None:
        _instance = TerminalPanel()
    return _instance
