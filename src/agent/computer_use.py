"""JARVIS Computer Use — Claude controls mouse/keyboard directly.

Uses Anthropic's Computer Use API (beta) to let Claude interact with
GUI applications: click buttons, type text, scroll, take screenshots.

This gives JARVIS the ability to automate any desktop application.
"""

import asyncio
import base64
import os
import subprocess
import logging

log = logging.getLogger("jarvis.computer_use")


def get_screen_size() -> tuple[int, int]:
    """Get screen resolution."""
    try:
        out = subprocess.run(
            ["xdpyinfo"], capture_output=True, text=True, timeout=3
        ).stdout
        for line in out.split("\n"):
            if "dimensions" in line:
                dim = line.split()[1]
                w, h = dim.split("x")
                return int(w), int(h)
    except Exception:
        pass
    return 1920, 1080


def take_screenshot() -> str:
    """Take a screenshot and return as base64."""
    path = "/tmp/jarvis_screen.png"
    try:
        import mss
        with mss.mss() as sct:
            sct.shot(output=path)
    except Exception:
        subprocess.run(["scrot", "-o", path], capture_output=True, timeout=5)

    if os.path.exists(path):
        with open(path, "rb") as f:
            return base64.standard_b64encode(f.read()).decode()
    return ""


def execute_computer_action(action: str, **kwargs) -> str:
    """Execute a computer use action."""
    try:
        if action == "screenshot":
            b64 = take_screenshot()
            return f"Screenshot taken ({len(b64)} bytes base64)"

        elif action == "click":
            x, y = kwargs.get("x", 0), kwargs.get("y", 0)
            button = kwargs.get("button", "left")
            btn_map = {"left": "1", "middle": "2", "right": "3"}
            subprocess.run(
                ["xdotool", "mousemove", str(x), str(y), "click", btn_map.get(button, "1")],
                capture_output=True, timeout=3,
                env={**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")}
            )
            return f"Clicked ({x}, {y}) button={button}"

        elif action == "type":
            text = kwargs.get("text", "")
            subprocess.run(
                ["xdotool", "type", "--clearmodifiers", text],
                capture_output=True, timeout=5,
                env={**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")}
            )
            return f"Typed: {text[:50]}"

        elif action == "key":
            key = kwargs.get("key", "")
            subprocess.run(
                ["xdotool", "key", key],
                capture_output=True, timeout=3,
                env={**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")}
            )
            return f"Key: {key}"

        elif action == "scroll":
            x, y = kwargs.get("x", 0), kwargs.get("y", 0)
            direction = kwargs.get("direction", "down")
            amount = kwargs.get("amount", 3)
            button = "5" if direction == "down" else "4"
            subprocess.run(
                ["xdotool", "mousemove", str(x), str(y)],
                capture_output=True, timeout=3,
                env={**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")}
            )
            for _ in range(amount):
                subprocess.run(
                    ["xdotool", "click", button],
                    capture_output=True, timeout=3,
                    env={**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")}
                )
            return f"Scrolled {direction} {amount}x at ({x}, {y})"

        elif action == "move":
            x, y = kwargs.get("x", 0), kwargs.get("y", 0)
            subprocess.run(
                ["xdotool", "mousemove", str(x), str(y)],
                capture_output=True, timeout=3,
                env={**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")}
            )
            return f"Moved to ({x}, {y})"

        else:
            return f"Unknown action: {action}"

    except Exception as e:
        return f"Computer use error: {e}"
