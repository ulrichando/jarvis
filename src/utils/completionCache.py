"""Shell completion setup and caching."""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _detect_shell() -> Optional[dict]:
    """Detect the current shell and return configuration info."""
    shell = os.environ.get("SHELL", "")
    home = str(Path.home())
    jarvis_dir = os.path.join(home, ".jarvis")

    if shell.endswith("/zsh") or shell.endswith("/zsh.exe"):
        cache_file = os.path.join(jarvis_dir, "completion.zsh")
        return {
            "name": "zsh",
            "rc_file": os.path.join(home, ".zshrc"),
            "cache_file": cache_file,
            "completion_line": f'[[ -f "{cache_file}" ]] && source "{cache_file}"',
            "shell_flag": "zsh",
        }
    elif shell.endswith("/bash") or shell.endswith("/bash.exe"):
        cache_file = os.path.join(jarvis_dir, "completion.bash")
        return {
            "name": "bash",
            "rc_file": os.path.join(home, ".bashrc"),
            "cache_file": cache_file,
            "completion_line": f'[ -f "{cache_file}" ] && source "{cache_file}"',
            "shell_flag": "bash",
        }
    elif shell.endswith("/fish") or shell.endswith("/fish.exe"):
        xdg = os.environ.get("XDG_CONFIG_HOME", os.path.join(home, ".config"))
        cache_file = os.path.join(jarvis_dir, "completion.fish")
        return {
            "name": "fish",
            "rc_file": os.path.join(xdg, "fish", "config.fish"),
            "cache_file": cache_file,
            "completion_line": f'[ -f "{cache_file}" ] && source "{cache_file}"',
            "shell_flag": "fish",
        }
    return None


async def setup_shell_completion() -> str:
    """Generate and cache completion script, add source line to shell rc file."""
    shell = _detect_shell()
    if not shell:
        return ""

    try:
        os.makedirs(os.path.dirname(shell["cache_file"]), exist_ok=True)
    except OSError as e:
        return f"\nCould not write {shell['name']} completion cache: {e}\n"

    # Try to generate completions
    try:
        result = subprocess.run(
            ["jarvis", "completion", shell["shell_flag"]],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            with open(shell["cache_file"], "w") as f:
                f.write(result.stdout)
        else:
            return f"\nCould not generate {shell['name']} shell completions\n"
    except FileNotFoundError:
        return f"\nCould not generate {shell['name']} shell completions\n"

    # Check if rc file already sources completions
    try:
        with open(shell["rc_file"]) as f:
            existing = f.read()
        if "jarvis completion" in existing or shell["cache_file"] in existing:
            return f"\nShell completions updated for {shell['name']}\n"
    except FileNotFoundError:
        existing = ""
    except OSError:
        return f"\nCould not install {shell['name']} shell completions\n"

    # Append source line
    try:
        os.makedirs(os.path.dirname(shell["rc_file"]), exist_ok=True)
        sep = "\n" if existing and not existing.endswith("\n") else ""
        content = f"{existing}{sep}\n# JARVIS shell completions\n{shell['completion_line']}\n"
        with open(shell["rc_file"], "w") as f:
            f.write(content)
        return f"\nInstalled {shell['name']} shell completions\n"
    except OSError:
        return f"\nCould not install {shell['name']} shell completions\nAdd this to {shell['rc_file']}:\n{shell['completion_line']}\n"


async def regenerate_completion_cache() -> None:
    """Regenerate cached shell completion scripts."""
    shell = _detect_shell()
    if not shell:
        return

    try:
        result = subprocess.run(
            ["jarvis", "completion", shell["shell_flag"]],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            with open(shell["cache_file"], "w") as f:
                f.write(result.stdout)
            logger.debug(f"Regenerated {shell['name']} completion cache")
        else:
            logger.debug(f"Failed to regenerate {shell['name']} completion cache")
    except Exception as e:
        logger.debug(f"Failed to regenerate completion cache: {e}")
