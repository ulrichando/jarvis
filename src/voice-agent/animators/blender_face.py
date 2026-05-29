#!/usr/bin/env python3
"""
Jarvis -> Blender Face Animator

Drives FaceCap ARKit blendshapes in Blender in sync with Jarvis's TTS output.
Monitors the voice-agent JSON log for [tts] Orpheus rendered events,
estimates audio duration, and animates the face in real-time.

ARKit blendshape mapping (standard 52-shape ordering):
  target_17 = jawOpen       (primary talking driver)
  target_18 = mouthClose
  target_19 = mouthFunnel
  target_20 = mouthPucker

Usage:
  python blender_face.py
  # Or from voice-agent dir:
  .venv/bin/python animators/blender_face.py

Env vars:
  BLENDER_HOST        — default localhost
  BLENDER_PORT        — default 9876
  VOICE_LOG_PATH      — default ~/.local/share/jarvis/logs/voice-agent.log
  FRAME_INTERVAL      — animation frame interval in seconds (default 0.033 = 30fps)
"""

import socket
import json
import subprocess
import time
import math
import os
import sys
import re
import logging
import threading
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [face-anim] %(levelname)s %(message)s",
)
logger = logging.getLogger("blender_face")

# -- Config ------------------------------------------------------------
BLENDER_HOST = os.getenv("BLENDER_HOST", "localhost")
BLENDER_PORT = int(os.getenv("BLENDER_PORT", "9876"))
VOICE_LOG_PATH = os.path.expanduser(
    os.getenv(
        "VOICE_LOG_PATH",
        "~/.local/share/jarvis/logs/voice-agent.log",
    )
)
FRAME_INTERVAL = float(os.getenv("FRAME_INTERVAL", "0.033"))  # ~30 fps

# Orpheus WAV: 24kHz mono 16-bit = 48,000 bytes/second
ORPHEUS_BYTES_PER_SEC = 48000

# Animation constants
JAW_SMOOTH_ATTACK = 0.20  # how fast jaw opens when speech starts
JAW_SMOOTH_DECAY = 0.12  # how fast jaw closes when speech ends
MOUTH_VARIATION_SPEED = 8.0  # Hz of natural mouth flutter during speech
MOUTH_VARIATION_AMOUNT = 0.12  # amplitude of flutter
JAW_MAX = 1.0  # maximum jaw openness

# ARKit blendshape indices
JAW_OPEN = 17
MOUTH_CLOSE = 18
MOUTH_FUNNEL = 19
MOUTH_PUCKER = 20

NEUTRAL = {JAW_OPEN: 0.0, MOUTH_CLOSE: 0.0, MOUTH_FUNNEL: 0.0, MOUTH_PUCKER: 0.0}


class BlenderConnection:
    """TCP connection to the Blender MCP addon."""

    def __init__(self, host=BLENDER_HOST, port=BLENDER_PORT):
        self.host = host
        self.port = port
        self.sock = None

    def connect(self):
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.connect((self.host, self.port))
            self.sock.settimeout(5)
            logger.info(f"Connected to Blender at {self.host}:{self.port}")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to Blender: {e}")
            self.sock = None
            return False

    def send(self, cmd_type, params=None):
        if self.sock is None:
            if not self.connect():
                return None
        command = {"type": cmd_type, "params": params or {}}
        try:
            self.sock.sendall(json.dumps(command).encode("utf-8"))
            chunks = []
            while True:
                chunk = self.sock.recv(8192)
                if not chunk:
                    break
                chunks.append(chunk)
                try:
                    json.loads(b"".join(chunks).decode("utf-8"))
                    break
                except json.JSONDecodeError:
                    continue
            return json.loads(b"".join(chunks).decode("utf-8"))
        except Exception as e:
            logger.error(f"Send error: {e}")
            self.sock = None
            return None

    def set_shape_keys(self, values):
        code_lines = [
            "import bpy",
            "mesh = bpy.data.objects.get('FaceCap_Head')",
            "if mesh and mesh.data.shape_keys:",
            "    sk = mesh.data.shape_keys.key_blocks",
        ]
        for idx, val in values.items():
            code_lines.append(f"    sk[{idx}].value = {val:.4f}")
        code_lines.append("    print('ok')")
        code = "\n".join(code_lines)
        result = self.send("execute_code", {"code": code})
        return result is not None

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None


class SpeechTracker:
    """Monitors the voice-agent log and tracks when Jarvis is speaking.

    Uses tail -f on the JSON log file. Parses [tts] Orpheus rendered
    events to know when speech starts and how long it lasts.
    """

    def __init__(self, log_path):
        self.log_path = log_path
        self.speaking_until = 0.0  # monotonic timestamp when current speech ends
        self.current_text = ""  # what Jarvis is currently saying
        self.lock = threading.Lock()
        self._running = False
        self._thread = None

        # Regex for [tts] Orpheus rendered log lines
        self.tts_re = re.compile(
            r"\[tts\] Orpheus rendered (\d+) bytes.*text='([^']*)'"
        )
        self.stop_re = re.compile(r"data-stop")

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._monitor_log, daemon=True)
        self._thread.start()
        logger.info(f"Monitoring voice log: {self.log_path}")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)

    def _monitor_log(self):
        """Tail the log file and parse speech events."""
        if not os.path.exists(self.log_path):
            logger.warning(f"Log file not found: {self.log_path}")
            return

        try:
            proc = subprocess.Popen(
                ["tail", "-n", "0", "-F", self.log_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,  # line-buffered
            )
        except Exception as e:
            logger.error(f"Failed to tail log: {e}")
            return

        try:
            for line in iter(proc.stdout.readline, ""):
                if not self._running:
                    break
                line = line.strip()
                if not line:
                    continue

                # Parse JSON log line
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                msg = entry.get("message", "")

                # Check for data-stop (interruption)
                if self.stop_re.search(msg):
                    with self.lock:
                        self.speaking_until = time.monotonic()
                        self.current_text = ""
                    logger.debug("Speech interrupted (data-stop)")
                    continue

                # Check for TTS render event
                match = self.tts_re.search(msg)
                if match:
                    bytes_count = int(match.group(1))
                    text = match.group(2)
                    duration = bytes_count / ORPHEUS_BYTES_PER_SEC
                    with self.lock:
                        self.speaking_until = time.monotonic() + duration
                        self.current_text = text
                    logger.info(
                        f"SPEECH: ~{duration:.1f}s — '{text}'"
                    )
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except Exception:
                proc.kill()

    def is_speaking(self):
        """Check if Jarvis is currently speaking."""
        with self.lock:
            return time.monotonic() < self.speaking_until


def main():
    logger.info("Jarvis -> Blender Face Animator starting")
    logger.info(f"Blender: {BLENDER_HOST}:{BLENDER_PORT}")
    logger.info(f"Log: {VOICE_LOG_PATH}")

    # Connect to Blender
    blender = BlenderConnection()
    if not blender.connect():
        logger.error(
            "Cannot connect to Blender. Is it running with the MCP addon?"
        )
        sys.exit(1)

    # Verify FaceCap_Head exists
    result = blender.send("execute_code", {
        "code": (
            "import bpy; obj = bpy.data.objects.get('FaceCap_Head'); "
            "print('YES' if obj and obj.data.shape_keys else 'NO')"
        )
    })
    if not result or "YES" not in str(result):
        logger.error(
            "FaceCap_Head with shape keys not found in Blender. "
            "Import the FaceCap model first: uid=29c2a506582a4157bf970bb8721a970c"
        )
        sys.exit(1)

    # Start monitoring voice log
    tracker = SpeechTracker(VOICE_LOG_PATH)
    tracker.start()

    logger.info("Ready - animating face when Jarvis speaks...")

    current_jaw = 0.0
    phase = 0.0
    current_values = dict(NEUTRAL)
    last_send = 0.0

    try:
        while True:
            is_speaking = tracker.is_speaking()

            if is_speaking:
                # Animate mouth with natural variation
                phase += MOUTH_VARIATION_SPEED * FRAME_INTERVAL
                flutter = (
                    math.sin(phase * 2 * math.pi) * MOUTH_VARIATION_AMOUNT
                )
                target_jaw = JAW_MAX + flutter
                smoothing = JAW_SMOOTH_ATTACK
            else:
                target_jaw = 0.0
                smoothing = JAW_SMOOTH_DECAY

            # Smooth jaw movement
            current_jaw += (target_jaw - current_jaw) * smoothing

            values = {
                JAW_OPEN: max(0.0, min(1.0, current_jaw)),
                MOUTH_CLOSE: max(0.0, 1.0 - current_jaw * 1.5),
                MOUTH_FUNNEL: current_jaw * 0.25,
                MOUTH_PUCKER: current_jaw * 0.1,
            }

            # Send to Blender if changed (throttled to ~30fps)
            now = time.monotonic()
            if now - last_send >= FRAME_INTERVAL:
                max_change = max(
                    abs(values[k] - current_values.get(k, 0))
                    for k in values
                )
                if max_change > 0.003:
                    blender.set_shape_keys(values)
                    current_values = values
                    last_send = now

            time.sleep(FRAME_INTERVAL)

    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        tracker.stop()
        blender.set_shape_keys(NEUTRAL)
        blender.close()
        logger.info("Face animator stopped, face restored to neutral")


if __name__ == "__main__":
    main()
