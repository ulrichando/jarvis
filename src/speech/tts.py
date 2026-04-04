"""Text-to-Speech — gives JARVIS a voice.

Uses piper-tts for neural speech synthesis (runs locally, no API needed).
Falls back to espeak-ng if piper isn't available.

Now supports chunked speech with natural pauses between phrases,
so JARVIS sounds like he's actually thinking, not reading a script.
"""

import subprocess
import tempfile
import time
from pathlib import Path
from src.config import TTS_MODEL
from src.speech.composer import plain_with_pauses

# Lazy-loaded
_piper_available = None


def _check_piper() -> bool:
    """Check if piper-tts is available."""
    global _piper_available
    if _piper_available is None:
        try:
            subprocess.run(["piper", "--help"], capture_output=True, timeout=5)
            _piper_available = True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            _piper_available = False
    return _piper_available


def speak(text: str):
    """Speak text aloud with natural pauses between phrases.

    Splits the text into chunks and plays each one with a
    pause in between — like a human taking breaths.
    """
    chunks = plain_with_pauses(text)

    if not chunks:
        return

    # If it's just one short chunk, speak directly
    if len(chunks) == 1:
        _speak_raw(chunks[0]["text"])
        return

    # Speak each chunk with pauses between them
    for i, chunk in enumerate(chunks):
        _speak_raw(chunk["text"])

        # Pause between chunks (but not after the last one)
        if i < len(chunks) - 1 and chunk["pause_after_ms"] > 0:
            time.sleep(chunk["pause_after_ms"] / 1000.0)


def _speak_raw(text: str):
    """Speak a single chunk of text."""
    if _check_piper():
        _speak_piper(text)
    else:
        _speak_espeak(text)


def _speak_piper(text: str):
    """Speak using piper-tts (neural, natural sounding)."""
    # Use delete=False so the file persists until we manually clean up
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp_path = tmp.name
    tmp.close()
    try:
        subprocess.run(
            ["piper", "--model", TTS_MODEL, "--output_file", tmp_path],
            input=text.encode(),
            capture_output=True,
            timeout=30,
        )
        subprocess.run(
            ["aplay", tmp_path],
            capture_output=True,
            timeout=30,
        )
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def _speak_espeak(text: str):
    """Speak using espeak-ng (robotic but universally available)."""
    subprocess.run(
        ["espeak-ng", "-v", "en", "-s", "160", text],
        capture_output=True,
        timeout=30,
    )


def text_to_audio_file(text: str, output_path: str) -> Path:
    """Generate speech and save to a file."""
    path = Path(output_path)
    if _check_piper():
        subprocess.run(
            ["piper", "--model", TTS_MODEL, "--output_file", str(path)],
            input=text.encode(),
            capture_output=True,
            timeout=30,
        )
    else:
        subprocess.run(
            ["espeak-ng", "-v", "en", "-w", str(path), text],
            capture_output=True,
            timeout=30,
        )
    return path
