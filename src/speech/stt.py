"""Speech-to-Text — Whisper-based transcription for JARVIS.

Uses faster-whisper (CTranslate2) for efficient local transcription.
Integrates with VAD for automatic speech boundary detection.
"""

import subprocess
import numpy as np
from src.config import STT_MODEL

# Lazy imports — these are heavy
_whisper_model = None


def _get_model():
    """Lazy-load the Whisper model.

    Uses tiny.en for speed (~0.4s) — good enough for voice commands.
    Force CPU to avoid CUDA OOM (GPU reserved for Ollama models).
    """
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        # Use tiny.en for speed — fast voice commands. Override STT_MODEL.
        fast_model = "tiny.en"
        _whisper_model = WhisperModel(
            fast_model,
            device="cpu",
            compute_type="int8",
            num_workers=2,
        )
    return _whisper_model


# Common Whisper hallucinations on silent/quiet audio
_HALLUCINATIONS = {
    "thank you for watching", "thanks for watching", "thank you for listening",
    "thanks for listening", "subscribe", "like and subscribe",
    "please subscribe", "see you next time", "bye", "goodbye",
    "thank you", "thanks", "you", "the end", "so", "okay",
    "um", "uh", "hmm", "ah", "oh", "i'm sorry",
    "subtitles by", "translated by", "amara.org",
    "transcribed by", "copyright", "all rights reserved",
    "music", "applause", "laughter", "silence",
    "...", "…", "♪", "♫",
    # Common noise hallucinations
    "mm-hmm", "mm", "mmm", "hmm", "huh", "shh",
    "no", "yes", "yeah", "yep", "nope", "right",
    "i see", "sure", "one", "two", "and",
    "i dare", "soon", "one second", "just",
    "go", "stop", "wait", "come", "what", "hm",
    "sigh", "cough", "sneeze", "breathing",
}


def _is_hallucination(text: str) -> bool:
    """Check if transcription is a known Whisper hallucination or noise."""
    t = text.lower().strip().rstrip(".!?,;:")
    if t in _HALLUCINATIONS:
        return True
    # Too short — likely noise
    if len(t) < 3:
        return True
    # Single word that isn't a command
    words = t.split()
    if len(words) <= 1:
        return True
    # Repeated phrases (e.g. "Thank you. Thank you. Thank you.")
    if len(words) >= 4:
        unique = set(words)
        if len(unique) <= 2:
            return True
    # All same word
    if len(set(words)) == 1:
        return True
    return False


def _has_speech_energy(audio: np.ndarray, threshold: float = 0.01) -> bool:
    """Check if audio has enough energy to contain speech."""
    rms = np.sqrt(np.mean(audio ** 2))
    return rms > threshold


def transcribe_audio(audio: np.ndarray, sample_rate: int = 16000) -> str:
    """Transcribe audio array to text.

    Args:
        audio: numpy array of audio samples (float32, mono)
        sample_rate: sample rate of audio (default 16000)

    Returns:
        Transcribed text, or empty string if no real speech detected
    """
    if not _has_speech_energy(audio):
        return ""

    model = _get_model()
    segments, info = model.transcribe(
        audio,
        beam_size=5,
        language="en",
        vad_filter=True,
        vad_parameters=dict(
            min_silence_duration_ms=500,
            speech_pad_ms=200,
        ),
    )

    parts = []
    for seg in segments:
        text = seg.text.strip()
        # Skip low-confidence or hallucinated segments
        if seg.no_speech_prob > 0.6:
            continue
        if _is_hallucination(text):
            continue
        parts.append(text)

    result = " ".join(parts)
    # Final hallucination check on combined result
    if _is_hallucination(result):
        return ""
    return result


def audio_bytes_to_numpy(audio_bytes: bytes) -> np.ndarray:
    """Convert audio bytes (webm/opus, mp4, wav, etc.) to 16kHz float32 mono numpy array.

    Uses ffmpeg via pipes — no temporary files.
    """
    cmd = [
        "ffmpeg", "-i", "pipe:0",
        "-f", "f32le",
        "-acodec", "pcm_f32le",
        "-ar", "16000",
        "-ac", "1",
        "-loglevel", "error",
        "pipe:1",
    ]
    proc = subprocess.run(cmd, input=audio_bytes, capture_output=True, timeout=15)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg conversion failed: {proc.stderr.decode()}")
    if len(proc.stdout) == 0:
        raise RuntimeError("ffmpeg produced no audio output")
    return np.frombuffer(proc.stdout, dtype=np.float32)


def transcribe_file(file_path: str) -> str:
    """Transcribe an audio file to text."""
    model = _get_model()
    segments, _ = model.transcribe(
        file_path,
        beam_size=5,
        language="en",
        vad_filter=True,
    )
    return " ".join(seg.text.strip() for seg in segments)
