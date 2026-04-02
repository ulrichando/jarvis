"""Voice Activity Detection — detects when the user is speaking.

Uses Silero VAD (tiny PyTorch model) to detect speech boundaries,
so JARVIS knows when to start and stop listening.
"""

import numpy as np
import sounddevice as sd

# VAD parameters
SAMPLE_RATE = 16000
FRAME_SIZE = 512  # ~32ms at 16kHz
SILENCE_THRESHOLD = 0.5  # VAD probability threshold
SILENCE_FRAMES = 30  # ~1 second of silence to stop

# Lazy-loaded model
_vad_model = None


def _get_vad():
    """Lazy-load Silero VAD."""
    global _vad_model
    if _vad_model is None:
        import torch
        model, utils = torch.hub.load(
            "snakers4/silero-vad", "silero_vad", trust_repo=True
        )
        _vad_model = model
    return _vad_model


def listen_until_silence(
    timeout: float = 30.0,
    silence_duration: float = 1.0,
) -> np.ndarray | None:
    """Listen to the microphone and return audio when the user stops speaking.

    Returns None if no speech detected within timeout.
    """
    import torch

    vad = _get_vad()
    frames: list[np.ndarray] = []
    silence_count = 0
    speech_detected = False
    silence_frames_needed = int(silence_duration * SAMPLE_RATE / FRAME_SIZE)
    max_frames = int(timeout * SAMPLE_RATE / FRAME_SIZE)

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                        blocksize=FRAME_SIZE) as stream:
        for _ in range(max_frames):
            audio_chunk, _ = stream.read(FRAME_SIZE)
            audio_chunk = audio_chunk.flatten()
            frames.append(audio_chunk)

            # Run VAD
            tensor = torch.from_numpy(audio_chunk)
            speech_prob = vad(tensor, SAMPLE_RATE).item()

            if speech_prob >= SILENCE_THRESHOLD:
                speech_detected = True
                silence_count = 0
            elif speech_detected:
                silence_count += 1
                if silence_count >= silence_frames_needed:
                    break

    if not speech_detected:
        return None

    return np.concatenate(frames).astype(np.float32)
