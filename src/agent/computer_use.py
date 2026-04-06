"""JARVIS Computer Use — desktop mouse/keyboard control.

Uses xdotool for input control and mss/scrot for screenshots to let
JARVIS interact with GUI applications: click buttons, type text, scroll,
take screenshots.

This gives JARVIS the ability to automate any desktop application.
"""

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

        elif action == "find_text":
            # OCR the active/focused window and find the coordinates of a text string
            target = kwargs.get("text", "")
            if not target:
                return "No text to find."
            path = "/tmp/jarvis_screen.png"
            window_offset_x, window_offset_y = 0, 0
            try:
                # Capture JUST the active window (not the whole screen)
                # This avoids OCR reading the terminal/VS Code behind Chrome
                env = {**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")}
                result = subprocess.run(
                    ["xdotool", "getactivewindow"],
                    capture_output=True, text=True, timeout=3, env=env
                )
                wid = result.stdout.strip()
                if wid:
                    # Get window geometry for coordinate offset
                    geo = subprocess.run(
                        ["xdotool", "getwindowgeometry", "--shell", wid],
                        capture_output=True, text=True, timeout=3, env=env
                    ).stdout
                    for line in geo.strip().split("\n"):
                        if line.startswith("X="):
                            window_offset_x = int(line.split("=")[1])
                        elif line.startswith("Y="):
                            window_offset_y = int(line.split("=")[1])
                    # Capture just this window
                    subprocess.run(
                        ["import", "-window", wid, path],
                        capture_output=True, timeout=5, env=env
                    )
            except Exception:
                pass
            # Fallback to full screen if window capture failed
            if not os.path.exists(path) or os.path.getsize(path) < 1000:
                try:
                    import mss
                    with mss.mss() as sct:
                        sct.shot(output=path)
                    window_offset_x, window_offset_y = 0, 0
                except Exception:
                    subprocess.run(["scrot", "-o", path], capture_output=True, timeout=5)
                    window_offset_x, window_offset_y = 0, 0
            try:
                import pytesseract
                from PIL import Image
                img = Image.open(path)
                data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT, timeout=10)
                target_lower = target.lower()
                matches = []
                # Try single-word match first
                for i, word in enumerate(data["text"]):
                    if word.strip() and target_lower in word.lower():
                        x = data["left"][i] + data["width"][i] // 2
                        y = data["top"][i] + data["height"][i] // 2
                        conf = data["conf"][i]
                        if int(conf) > 30:
                            matches.append({"text": word, "x": x, "y": y, "conf": conf})
                # Try multi-word match (combine adjacent words)
                if not matches and " " in target:
                    words = data["text"]
                    for i in range(len(words)):
                        combined = ""
                        for j in range(i, min(i + len(target.split()) + 2, len(words))):
                            if words[j].strip():
                                combined += (" " if combined else "") + words[j]
                            if target_lower in combined.lower():
                                # Center of the matched region
                                x = (data["left"][i] + data["left"][j] + data["width"][j]) // 2
                                y = (data["top"][i] + data["top"][j] + data["height"][j]) // 2
                                matches.append({"text": combined.strip(), "x": x, "y": y, "conf": 80})
                                break
                if matches:
                    best = max(matches, key=lambda m: int(m["conf"]))
                    # Convert window-relative coordinates to absolute screen coordinates
                    abs_x = best['x'] + window_offset_x
                    abs_y = best['y'] + window_offset_y
                    result = f"Found \"{best['text']}\" at ({abs_x}, {abs_y})"
                    if len(matches) > 1:
                        result += f" (+{len(matches)-1} other matches)"
                    return result
                return f"Text \"{target}\" not found on screen."
            except ImportError:
                return "pytesseract or PIL not installed."
            except Exception as e:
                return f"OCR error: {e}"

        elif action == "double_click":
            x, y = kwargs.get("x", 0), kwargs.get("y", 0)
            subprocess.run(
                ["xdotool", "mousemove", str(x), str(y), "click", "--repeat", "2", "1"],
                capture_output=True, timeout=3,
                env={**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")}
            )
            return f"Double-clicked ({x}, {y})"

        else:
            return f"Unknown action: {action}"

    except Exception as e:
        return f"Computer use error: {e}"
