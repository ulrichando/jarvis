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
    """Lazy-load the Whisper model. Auto-selects device based on hardware."""
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel

        # Auto-detect best device
        device = "cpu"
        compute = "int8"
        try:
            from src.hardware import detect_hardware
            hw = detect_hardware()
            if hw.has_cuda:
                device = "cuda"
                compute = "float16"
        except Exception:
            pass

        # Use large-v3-turbo for best accuracy with accents and proper nouns
        # Fast on GPU (RTX 2060+), best transcription quality available
        fast_model = "large-v3-turbo"
        _whisper_model = WhisperModel(
            fast_model,
            device=device,
            compute_type=compute,
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


import concurrent.futures
_transcription_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="whisper")


def _transcribe_sync(audio: np.ndarray, sample_rate: int) -> str:
    """Synchronous transcription — runs in a thread pool with timeout."""
    model = _get_model()
    segments, info = model.transcribe(
        audio,
        beam_size=5,
        language="en",
        vad_filter=True,
        condition_on_previous_text=False,  # Prevents decoder loops
        vad_parameters=dict(
            min_silence_duration_ms=500,
            speech_pad_ms=200,
        ),
    )

    parts = []
    for seg in segments:
        text = seg.text.strip()
        if seg.no_speech_prob > 0.6:
            continue
        if _is_hallucination(text):
            continue
        parts.append(text)

    result = " ".join(parts)
    if _is_hallucination(result):
        return ""
    return result


def transcribe_audio(audio: np.ndarray, sample_rate: int = 16000, timeout: float = 15.0) -> str:
    """Transcribe audio with timeout protection.

    Uses a thread pool so a stuck Whisper model can't block the server.
    """
    if not _has_speech_energy(audio):
        return ""

    # Cap audio to 30 seconds max
    max_samples = int(30.0 * sample_rate)
    if len(audio) > max_samples:
        audio = audio[:max_samples]

    try:
        future = _transcription_pool.submit(_transcribe_sync, audio, sample_rate)
        return future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        print("[JARVIS] Whisper transcription timed out")
        return ""
    except Exception as e:
        print(f"[JARVIS] Whisper error: {e}")
        return ""


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
