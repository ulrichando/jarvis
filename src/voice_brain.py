"""JARVIS Voice — VAD, config, and voice input."""

import shutil
import struct
from dataclasses import dataclass, field


@dataclass
class VoiceConfig:
    sample_rate: int = 16000
    language: str = "en"
    tts_voice: str = "en-US-GuyNeural"
    vad_threshold: float = 0.03
    silence_duration: float = 1.5


def detect_speech_activity(audio_bytes: bytes, threshold: float = 0.03) -> bool:
    """Simple energy-based VAD. Returns True if RMS exceeds threshold."""
    if len(audio_bytes) < 2:
        return False
    n_samples = len(audio_bytes) // 2
    samples = struct.unpack(f"<{n_samples}h", audio_bytes[:n_samples * 2])
    rms = (sum(s * s for s in samples) / n_samples) ** 0.5
    normalized = rms / 32768.0
    return normalized > threshold


class VoiceInput:
    """Checks mic availability and records audio."""

    def is_available(self) -> bool:
        return shutil.which("arecord") is not None or shutil.which("sox") is not None
