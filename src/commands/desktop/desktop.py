"""Desktop command implementation — launch JARVIS GTK overlay."""

from __future__ import annotations

import os
import subprocess
from typing import Any


def _is_desktop_running() -> bool:
    """Check if a JARVIS desktop process is already running."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "src.desktop.app"],
            capture_output=True, timeout=3,
        )
        return result.returncode == 0
    except Exception:
        return False


async def call(on_done: Any = None, context: Any = None, **_kwargs: Any) -> None:
    """Launch the JARVIS desktop overlay."""
    if _is_desktop_running():
        if on_done:
            on_done("JARVIS desktop is already running.", {"display": "system"})
        return

    jarvis_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    )
    log_path = "/tmp/jarvis-desktop.log"

    with open(log_path, "w") as log_file:
        subprocess.Popen(
            ["python3", "-c", "from src.desktop.app import main; main()"],
            cwd=jarvis_root,
            start_new_session=True,
            stdout=log_file,
            stderr=log_file,
            env={**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0.0")},
        )

    if on_done:
        on_done(
            f"JARVIS desktop launching... (log: {log_path})",
            {"display": "system"},
        )
