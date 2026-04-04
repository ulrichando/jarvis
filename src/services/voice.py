"""
Voice service: audio recording for push-to-talk voice input.

Recording uses native audio capture or falls back to SoX/arecord
on Linux for microphone access.
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

RECORDING_SAMPLE_RATE = 16000
RECORDING_CHANNELS = 1
SILENCE_DURATION_SECS = "2.0"
SILENCE_THRESHOLD = "3%"

# Module-level state
_active_recorder: Optional[subprocess.Popen] = None


def _has_command(cmd: str) -> bool:
    """Check if a command is available on the system."""
    return shutil.which(cmd) is not None


@dataclass
class RecordingAvailability:
    available: bool
    reason: Optional[str] = None


@dataclass
class VoiceDependencies:
    available: bool
    missing: List[str]
    install_command: Optional[str] = None


def _detect_package_manager() -> Optional[Dict[str, str]]:
    """Detect the system package manager."""
    system = platform.system()

    if system == "Darwin":
        if _has_command("brew"):
            return {"cmd": "brew install sox", "display": "brew install sox"}
        return None

    if system == "Linux":
        if _has_command("apt-get"):
            return {"cmd": "sudo apt-get install -y sox", "display": "sudo apt-get install sox"}
        if _has_command("dnf"):
            return {"cmd": "sudo dnf install -y sox", "display": "sudo dnf install sox"}
        if _has_command("pacman"):
            return {"cmd": "sudo pacman -S --noconfirm sox", "display": "sudo pacman -S sox"}

    return None


async def check_voice_dependencies() -> VoiceDependencies:
    """Check if voice recording dependencies are available."""
    # Check for arecord (ALSA utils) on Linux
    if platform.system() == "Linux" and _has_command("arecord"):
        return VoiceDependencies(available=True, missing=[])

    # Check for SoX
    if _has_command("rec"):
        return VoiceDependencies(available=True, missing=[])

    missing = ["sox (rec command)"]
    pm = _detect_package_manager()
    return VoiceDependencies(
        available=False,
        missing=missing,
        install_command=pm["display"] if pm else None,
    )


async def check_recording_availability() -> RecordingAvailability:
    """Check if audio recording is available."""
    # Remote environments have no local microphone
    if os.environ.get("CLAUDE_CODE_REMOTE"):
        return RecordingAvailability(
            available=False,
            reason="Voice mode requires microphone access, not available in remote environments.",
        )

    # Check for arecord on Linux
    if platform.system() == "Linux" and _has_command("arecord"):
        return RecordingAvailability(available=True)

    # Check for SoX
    if _has_command("rec"):
        return RecordingAvailability(available=True)

    pm = _detect_package_manager()
    if pm:
        return RecordingAvailability(
            available=False,
            reason=f"Voice mode requires SoX. Install with: {pm['display']}",
        )

    return RecordingAvailability(
        available=False,
        reason=(
            "Voice mode requires SoX for audio recording. Install SoX manually:\n"
            "  macOS: brew install sox\n"
            "  Ubuntu/Debian: sudo apt-get install sox\n"
            "  Fedora: sudo dnf install sox"
        ),
    )


def start_recording(
    on_data: Callable[[bytes], None],
    on_end: Callable[[], None],
    silence_detection: bool = True,
) -> bool:
    """Start audio recording.

    Uses arecord (ALSA) on Linux, or SoX rec as fallback.
    Returns True if recording started successfully.
    """
    global _active_recorder

    # Try arecord on Linux
    if platform.system() == "Linux" and _has_command("arecord"):
        return _start_arecord_recording(on_data, on_end)

    # Fallback: SoX rec
    return _start_sox_recording(on_data, on_end, silence_detection)


def _start_sox_recording(
    on_data: Callable[[bytes], None],
    on_end: Callable[[], None],
    silence_detection: bool = True,
) -> bool:
    """Start recording using SoX rec command."""
    global _active_recorder

    args = [
        "rec", "-q", "--buffer", "1024",
        "-t", "raw", "-r", str(RECORDING_SAMPLE_RATE),
        "-e", "signed", "-b", "16", "-c", str(RECORDING_CHANNELS),
        "-",  # stdout
    ]

    if silence_detection:
        args.extend([
            "silence", "1", "0.1", SILENCE_THRESHOLD,
            "1", SILENCE_DURATION_SECS, SILENCE_THRESHOLD,
        ])

    try:
        proc = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        _active_recorder = proc

        import threading

        def _read_output():
            try:
                while proc.stdout and proc.returncode is None:
                    chunk = proc.stdout.read(4096)
                    if not chunk:
                        break
                    on_data(chunk)
            finally:
                on_end()

        t = threading.Thread(target=_read_output, daemon=True)
        t.start()
        return True
    except Exception as e:
        logger.error(f"Failed to start SoX recording: {e}")
        return False


def _start_arecord_recording(
    on_data: Callable[[bytes], None],
    on_end: Callable[[], None],
) -> bool:
    """Start recording using ALSA arecord."""
    global _active_recorder

    args = [
        "arecord",
        "-f", "S16_LE",
        "-r", str(RECORDING_SAMPLE_RATE),
        "-c", str(RECORDING_CHANNELS),
        "-t", "raw", "-q",
        "-",  # stdout
    ]

    try:
        proc = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        _active_recorder = proc

        import threading

        def _read_output():
            try:
                while proc.stdout and proc.returncode is None:
                    chunk = proc.stdout.read(4096)
                    if not chunk:
                        break
                    on_data(chunk)
            finally:
                on_end()

        t = threading.Thread(target=_read_output, daemon=True)
        t.start()
        return True
    except Exception as e:
        logger.error(f"Failed to start arecord: {e}")
        return False


def stop_recording() -> None:
    """Stop the active recording."""
    global _active_recorder
    if _active_recorder is not None:
        try:
            _active_recorder.terminate()
        except Exception:
            pass
        _active_recorder = None
