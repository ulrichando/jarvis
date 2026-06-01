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
<<<<<<< HEAD
import os
import sys
import re
import signal
=======
import math
import os
import sys
import re
>>>>>>> origin/master
import logging
import threading
from pathlib import Path

<<<<<<< HEAD
# Make `animators` importable whether this is run as a script
# (python animators/blender_face.py), a module (-m animators.blender_face),
# or imported — the script's own dir is animators/, so its parent (the
# voice-agent root) must be on the path for `from animators import ...`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from animators import face_anim_core as fac
from animators import blender_scene_setup, blender_frame_server
from animators.loudness_monitor import LoudnessMonitor

=======
>>>>>>> origin/master
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

<<<<<<< HEAD
# Jaw smoothing (asymmetric: opens faster than it closes — reads as speech).
JAW_SMOOTH_ATTACK = 0.20  # how fast jaw opens when speech starts
JAW_SMOOTH_DECAY = 0.12  # how fast jaw closes when speech ends

# NOTE: shape-key resolution is by NAME at runtime (face_anim_core.resolve_key_names
# + BlenderConnection.get_shape_key_names). jawOpen resolves to FaceCap's
# `target_24`, confirmed empirically — do NOT reintroduce hardcoded ARKit indices
# (the prototype's target_17 was an eye shape, so its face never talked).

# Per-frame shape-key values go through this tiny shared file, NOT the Blender
# socket: execute_code round-trips are ~1s/call (hopeless for animation). The
# frame server's render timer reads this file and applies it inside Blender just
# before each render. The socket is used only once at startup (install + resolve).
SHAPES_PATH = "/dev/shm/jarvis_face_shapes.json"


def write_shapes(values):
    """Atomically write {shape_key_name: value} for the Blender frame server."""
    tmp = SHAPES_PATH + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(values, f)
        os.replace(tmp, SHAPES_PATH)
    except OSError:
        pass
=======
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
>>>>>>> origin/master


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
<<<<<<< HEAD
        """Set shape keys BY NAME. `values` is {shape_key_name: float}."""
=======
>>>>>>> origin/master
        code_lines = [
            "import bpy",
            "mesh = bpy.data.objects.get('FaceCap_Head')",
            "if mesh and mesh.data.shape_keys:",
            "    sk = mesh.data.shape_keys.key_blocks",
        ]
<<<<<<< HEAD
        for name, val in values.items():
            code_lines.append(
                f"    if {name!r} in sk: sk[{name!r}].value = {val:.4f}")
=======
        for idx, val in values.items():
            code_lines.append(f"    sk[{idx}].value = {val:.4f}")
>>>>>>> origin/master
        code_lines.append("    print('ok')")
        code = "\n".join(code_lines)
        result = self.send("execute_code", {"code": code})
        return result is not None

<<<<<<< HEAD
    def get_shape_key_names(self):
        """Return the live FaceCap_Head key_blocks names (for name resolution)."""
        code = (
            "import bpy, json\n"
            "o = bpy.data.objects.get('FaceCap_Head')\n"
            "kb = o.data.shape_keys.key_blocks if o and o.data.shape_keys else []\n"
            "print('KEYS:' + json.dumps([k.name for k in kb]))\n"
        )
        result = self.send("execute_code", {"code": code})
        text = str(result) if result is not None else ""
        match = re.search(r"KEYS:(\[.*?\])", text)
        if not match:
            return []
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            return []

=======
>>>>>>> origin/master
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

<<<<<<< HEAD
    # Install / refresh the scene (idempotent) and the MJPEG frame server.
    logger.info("Installing Blender scene + frame server...")
    scene_res = blender_scene_setup.install(blender)
    logger.info("Scene setup: %s", scene_res)
    if not scene_res or "NO_HEAD" in str(scene_res):
        logger.error(
            "FaceCap_Head not found. Import the FaceCap model first: "
            "uid=%s", blender_scene_setup.FACECAP_UID)
        sys.exit(1)
    blender_frame_server.install(blender)

    # Resolve the ARKit names we drive to the head's actual shape-key names
    # (FaceCap uses target_N aliases; jawOpen=target_24 confirmed empirically).
    key_names = blender.get_shape_key_names()
    name_map = fac.resolve_key_names(["jawOpen"], key_names)
    if "jawOpen" not in name_map:
        logger.error("FaceCap_Head exposes no jawOpen/target_24 shape key "
                     "(found %d keys). Cannot animate.", len(key_names))
        sys.exit(1)
    logger.info("Resolved shape keys: %s", name_map)
    neutral = {actual: 0.0 for actual in name_map.values()}

    # Loudness tap (PipeWire) is the PRIMARY driver. The log tracker still runs
    # for debug visibility ("SPEECH: ..." lines) but does NOT gate the jaw —
    # `tail -F` added ~2.4s latency, which breaks lip-sync. JARVIS's output sink
    # (echo-cancel-sink.monitor) is silent when it isn't speaking, so loudness
    # is its own immediate gate and closes the mouth automatically on silence /
    # barge-in.
    tracker = SpeechTracker(VOICE_LOG_PATH)
    tracker.start()
    loudness = LoudnessMonitor()
    loudness.start()
    jaw_gain = float(os.getenv("JARVIS_FACE_JAW_GAIN", "6.0"))
    gate_level = float(os.getenv("JARVIS_FACE_GATE_LEVEL", "0.02"))

    logger.info("Ready - jaw tracks JARVIS loudness (gate=%.3f gain=%.1f)...",
                gate_level, jaw_gain)

    # Clean shutdown on SIGINT/SIGTERM so the finally block neutralizes the
    # mouth (otherwise the frame server holds the last jaw value).
    stop = {"flag": False}

    def _on_signal(signum, frame):
        stop["flag"] = True
    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    debug = os.getenv("JARVIS_FACE_DEBUG") == "1"
    current_jaw = 0.0
    current_values = {}
    last_send = 0.0
    last_log = 0.0

    try:
        while not stop["flag"]:
            level = loudness.level()
            speaking = level > gate_level          # immediate loudness gate
            target = fac.target_jaw(speaking, level, gain=jaw_gain)
            current_jaw = fac.smooth_jaw(
                current_jaw, target,
                attack=JAW_SMOOTH_ATTACK, decay=JAW_SMOOTH_DECAY)

            shapes = fac.shape_values(current_jaw)          # {arkit_name: value}
            values = {name_map[k]: v for k, v in shapes.items()
                      if k in name_map}                     # -> {target_24: value}

            now = time.monotonic()
            if debug and now - last_log >= 1.0:
                logger.info("level=%.3f speaking=%s target=%.3f jaw=%.3f vals=%s",
                            level, speaking, target, current_jaw, values)
                last_log = now

            # Write the shapes file ~30fps (cheap); the frame server applies it.
            if now - last_send >= FRAME_INTERVAL:
                max_change = max(
                    (abs(values[k] - current_values.get(k, 0.0)) for k in values),
                    default=0.0)
                if max_change > 0.001:
                    write_shapes(values)
=======
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
>>>>>>> origin/master
                    current_values = values
                    last_send = now

            time.sleep(FRAME_INTERVAL)

    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
<<<<<<< HEAD
        loudness.stop()
        tracker.stop()
        write_shapes(neutral)      # frame server applies neutral next tick
=======
        tracker.stop()
        blender.set_shape_keys(NEUTRAL)
>>>>>>> origin/master
        blender.close()
        logger.info("Face animator stopped, face restored to neutral")


if __name__ == "__main__":
    main()
