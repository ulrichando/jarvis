"""Real-time loudness of JARVIS's voice via a PipeWire monitor tap.

`rms_level` is pure and unit-tested. `LoudnessMonitor` runs a background
thread reading signed-16 mono PCM chunks from a frame source (by default a
`parec` subprocess on the output sink's .monitor) and exposes a smoothed
0..1 level. The frame source is injectable for testing.
"""

import math
import os
import shutil
import struct
import subprocess
import threading


def rms_level(pcm_s16: bytes, gain: float = 4.0, floor: float = 0.004) -> float:
    """RMS of signed-16 mono PCM -> normalized 0..1 level.

    rms is divided by 32768 to land in 0..1, the noise floor is subtracted,
    the result scaled by gain and clamped.
    """
    n = len(pcm_s16) // 2
    if n == 0:
        return 0.0
    samples = struct.unpack(f"<{n}h", pcm_s16[: n * 2])
    mean_sq = sum(s * s for s in samples) / n
    rms = math.sqrt(mean_sq) / 32768.0
    level = (rms - floor) * gain
    return max(0.0, min(1.0, level))


def default_monitor_device() -> str:
    """Best-effort name of the default sink's monitor source.

    Honors $JARVIS_FACE_AUDIO_MONITOR; otherwise asks pactl for the default
    sink and appends '.monitor'. Returns '' if it can't be determined (the
    caller then lets parec pick the default source).
    """
    override = os.getenv("JARVIS_FACE_AUDIO_MONITOR")
    if override:
        return override
    if shutil.which("pactl"):
        try:
            sink = subprocess.check_output(
                ["pactl", "get-default-sink"], text=True, timeout=2
            ).strip()
            if sink:
                return f"{sink}.monitor"
        except Exception:
            pass
    return ""


# bytes per read: ~512 samples * 2 bytes
_CHUNK = 1024


def _parec_source(device: str):
    """Return a callable yielding PCM chunks from a parec subprocess."""
    cmd = ["parec", "--format=s16le", "--rate=16000", "--channels=1"]
    if device:
        cmd += [f"--device={device}"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL)

    def read():
        if proc.stdout is None:
            return b""
        return proc.stdout.read(_CHUNK)

    read._proc = proc  # type: ignore[attr-defined]
    return read


class LoudnessMonitor:
    """Background RMS reader exposing a smoothed 0..1 level()."""

    def __init__(self, frame_source=None, gain: float = 6.0,
                 floor: float = 0.004, ema: float = 0.4,
                 degraded_level: float = 0.75):
        self._frame_source = frame_source  # callable()->bytes, or None
        self._gain = gain
        self._floor = floor
        self._ema = ema  # 1.0 = no smoothing
        self._degraded_level = degraded_level
        self._level = 0.0
        self._degraded = False
        self._running = False
        self._thread = None
        self._lock = threading.Lock()

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)

    def level(self) -> float:
        with self._lock:
            if self._degraded:
                return self._degraded_level
            return self._level

    def _ensure_source(self):
        if self._frame_source is not None:
            return self._frame_source
        device = default_monitor_device()
        self._frame_source = _parec_source(device)
        return self._frame_source

    def _run(self):
        try:
            source = self._ensure_source()
        except Exception:
            with self._lock:
                self._degraded = True
            return
        while self._running:
            try:
                chunk = source()
            except Exception:
                with self._lock:
                    self._degraded = True
                return
            if not chunk:
                # end-of-stream / no data: brief idle, keep last level
                continue
            lvl = rms_level(chunk, gain=self._gain, floor=self._floor)
            with self._lock:
                self._level = (self._ema * lvl
                               + (1.0 - self._ema) * self._level)
