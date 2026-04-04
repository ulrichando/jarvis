"""JARVIS Command Executor — full system control for Ulrich."""

import subprocess
import shlex
import os


class CommandExecutor:
    """Executes system commands. No restrictions — Ulrich has full control."""

    def __init__(self, safety_mode: bool = False):
        self.safety_mode = safety_mode

    def execute(self, command: str, timeout: int = 30) -> dict:
        """Execute a shell command and return the result."""
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                env={**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")},
            )
            return {
                "output": result.stdout or result.stderr,
                "exit_code": result.returncode,
                "success": result.returncode == 0,
            }
        except subprocess.TimeoutExpired:
            return {"output": f"Timed out after {timeout}s", "exit_code": -1, "success": False}
        except Exception as e:
            return {"output": str(e), "exit_code": -1, "success": False}

    def open_app(self, app_name: str) -> dict:
        """Open an application by name."""
        # Use setsid to fully detach the process
        return self.execute(f"setsid {shlex.quote(app_name)} &>/dev/null &")

    def open_url(self, url: str) -> dict:
        """Open a URL in the default browser."""
        return self.execute(f"xdg-open {shlex.quote(url)} &>/dev/null &")

    def open_folder(self, path: str) -> dict:
        """Open a folder in the file manager."""
        return self.execute(f"xdg-open {shlex.quote(path)} &>/dev/null &")
