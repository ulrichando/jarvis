"""JARVIS Hardware Detection — auto-detect and adapt to available hardware.

Runs on startup. Detects cameras (RGB/IR), microphones, GPUs, fingerprint readers,
displays, and audio devices. Stores results so other modules can adapt.

Usage:
    from src.hardware import detect_hardware, HW
    hw = detect_hardware()  # cached after first call
    if hw.has_ir_camera: ...
    if hw.has_gpu: ...
"""

import subprocess
import os
from dataclasses import dataclass, field


@dataclass
class HardwareProfile:
    """Detected hardware capabilities."""
    # Cameras
    cameras: list = field(default_factory=list)     # [{"id": 0, "type": "rgb", "res": "640x480"}, ...]
    has_rgb_camera: bool = False
    has_ir_camera: bool = False
    rgb_camera_id: int = 0
    ir_camera_id: int = 2

    # Audio
    has_microphone: bool = False
    has_speakers: bool = False
    audio_backend: str = ""  # "pipewire", "pulseaudio", "alsa"

    # Biometrics
    has_fingerprint: bool = False
    fingerprint_vendor: str = ""

    # GPU
    has_gpu: bool = False
    gpu_name: str = ""
    has_nvidia: bool = False
    has_cuda: bool = False

    # Display
    display_resolution: str = ""
    display_server: str = ""  # "x11", "wayland"

    # Memory
    total_ram_gb: float = 0.0
    available_ram_gb: float = 0.0

    # ── Adaptation: what features should be enabled? ──
    @property
    def can_voice_input(self) -> bool:
        return self.has_microphone

    @property
    def can_voice_output(self) -> bool:
        return self.has_speakers

    @property
    def can_vision(self) -> bool:
        return self.has_rgb_camera or self.has_ir_camera

    @property
    def can_face_id(self) -> bool:
        return self.has_ir_camera

    @property
    def can_desktop(self) -> bool:
        return bool(self.display_server)

    @property
    def whisper_device(self) -> str:
        """Best device for Whisper STT."""
        return "cuda" if self.has_cuda else "cpu"

    @property
    def recommended_model_size(self) -> str:
        """Largest local model that fits in RAM."""
        if self.available_ram_gb >= 40:
            return "70b"
        elif self.available_ram_gb >= 20:
            return "32b"
        elif self.available_ram_gb >= 8:
            return "7b"
        else:
            return "3b"

    @property
    def tts_engine(self) -> str:
        """Best TTS engine: piper (local) if no network assumed, else edge."""
        return "edge"  # edge is default, piper as fallback

    # Summary for LLM context
    def summary(self) -> str:
        parts = []
        if self.cameras:
            cam_types = [c["type"] for c in self.cameras]
            parts.append(f"Cameras: {', '.join(cam_types)}")
        if self.has_ir_camera:
            parts.append("IR face ID camera available")
        if self.has_fingerprint:
            parts.append(f"Fingerprint reader ({self.fingerprint_vendor})")
        if self.has_nvidia:
            parts.append(f"GPU: {self.gpu_name}")
        if self.has_microphone:
            parts.append("Microphone active")
        parts.append(f"RAM: {self.total_ram_gb:.0f}GB")
        return " | ".join(parts)


# Cached instance
_hw: HardwareProfile | None = None


def _probe_cameras(hw: "HardwareProfile"):
    """Probe camera devices — called lazily, only when vision is needed."""
    try:
        import cv2
        os.environ.setdefault("OPENCV_LOG_LEVEL", "SILENT")
        cv2.setLogLevel(0)
        for i in range(5):
            cap = cv2.VideoCapture(i, cv2.CAP_V4L2)
            if cap.isOpened():
                w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                cap.release()
                cam_type = "ir" if (w == 576 and h == 360 and i >= 2) else "rgb"
                hw.cameras.append({"id": i, "type": cam_type, "res": f"{w}x{h}"})
                if cam_type == "rgb" and not hw.has_rgb_camera:
                    hw.has_rgb_camera = True
                    hw.rgb_camera_id = i
                if cam_type == "ir":
                    hw.has_ir_camera = True
                    hw.ir_camera_id = i
            else:
                cap.release()
    except Exception:
        pass


def detect_hardware(include_cameras: bool = False) -> HardwareProfile:
    """Detect hardware — cached after first call.

    Cameras are NOT probed on startup (expensive, noisy). Pass include_cameras=True
    only when vision is actually needed — e.g. when the LLM calls a camera tool.
    """
    global _hw
    if _hw is not None:
        # If cameras were skipped before but are now requested, probe them now
        if include_cameras and not _hw.cameras:
            _probe_cameras(_hw)
        return _hw

    hw = HardwareProfile()

    if include_cameras:
        _probe_cameras(hw)

    # ── Fingerprint ──
    try:
        r = subprocess.run(["lsusb"], capture_output=True, text=True, timeout=5)
        for line in r.stdout.split("\n"):
            ll = line.lower()
            if "fingerprint" in ll or "goodix" in ll:
                hw.has_fingerprint = True
                if "goodix" in ll:
                    hw.fingerprint_vendor = "Goodix"
                else:
                    hw.fingerprint_vendor = line.split(":")[-1].strip()[:30]
    except Exception:
        pass

    # ── GPU ──
    try:
        r = subprocess.run(["lspci"], capture_output=True, text=True, timeout=5)
        for line in r.stdout.split("\n"):
            if "VGA" in line or "3D" in line or "Display" in line:
                if "nvidia" in line.lower():
                    hw.has_gpu = True
                    hw.has_nvidia = True
                    hw.gpu_name = line.split(":")[-1].strip()[:50]
                elif "intel" in line.lower() or "amd" in line.lower():
                    if not hw.gpu_name:  # nvidia takes priority
                        hw.has_gpu = True
                        hw.gpu_name = line.split(":")[-1].strip()[:50]
    except Exception:
        pass

    # Check CUDA
    try:
        r = subprocess.run(["nvidia-smi"], capture_output=True, text=True, timeout=5)
        hw.has_cuda = r.returncode == 0
    except Exception:
        pass

    # ── Audio ──
    try:
        # Check mic
        r = subprocess.run(["arecord", "-l"], capture_output=True, text=True, timeout=3)
        hw.has_microphone = "card" in r.stdout.lower()

        # Check speakers
        r = subprocess.run(["aplay", "-l"], capture_output=True, text=True, timeout=3)
        hw.has_speakers = "card" in r.stdout.lower()

        # Audio backend
        r = subprocess.run(["pactl", "info"], capture_output=True, text=True, timeout=3)
        if "PipeWire" in r.stdout:
            hw.audio_backend = "pipewire"
        elif "PulseAudio" in r.stdout:
            hw.audio_backend = "pulseaudio"
        else:
            hw.audio_backend = "alsa"
    except Exception:
        hw.audio_backend = "unknown"

    # ── Display ──
    try:
        if os.environ.get("WAYLAND_DISPLAY"):
            hw.display_server = "wayland"
        elif os.environ.get("DISPLAY"):
            hw.display_server = "x11"

        r = subprocess.run(["xrandr"], capture_output=True, text=True, timeout=3)
        for line in r.stdout.split("\n"):
            if " connected" in line and "primary" in line:
                parts = line.split()
                for p in parts:
                    if "x" in p and "+" in p:
                        hw.display_resolution = p.split("+")[0]
                        break
    except Exception:
        pass

    # ── Memory ──
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    hw.total_ram_gb = int(line.split()[1]) / 1024 / 1024
                elif line.startswith("MemAvailable:"):
                    hw.available_ram_gb = int(line.split()[1]) / 1024 / 1024
    except Exception:
        pass

    _hw = hw
    return hw


# Convenience accessor
HW = detect_hardware
