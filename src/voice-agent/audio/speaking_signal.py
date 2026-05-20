"""Detect whether an OUTGOING TTS PCM frame is speech (vs the always-open
silent track). Used to drive state.speaking off JARVIS's own clean audio
instead of the mic-side RMS, so the mic-drop fallback never false-mutes the
user. Spec 2026-05-20 §5.5."""
from __future__ import annotations
import os
import numpy as np

# Orpheus speech sits well above this; the always-open silent track is ~0.
_SPEECH_PCM_RMS = float(os.environ.get("JARVIS_SPEAKING_PCM_RMS", "300"))


def is_rendering_speech(pcm_int16: np.ndarray) -> bool:
    if pcm_int16 is None or len(pcm_int16) == 0:
        return False
    rms = float(np.sqrt(np.mean(pcm_int16.astype(np.float32) ** 2)))
    return rms > _SPEECH_PCM_RMS
