"""Voice Data Collector — captures audio + transcription pairs for fine-tuning.

Runs passively as JARVIS is used. Every time the STT transcribes speech,
the audio and text are saved as a training pair. After enough data is
collected (~30-60 min), the fine-tuning pipeline can be run.

Storage: ~/.jarvis/voice_data/
  ├── manifest.jsonl          # {audio_path, text, duration, timestamp}
  ├── audio/
  │   ├── 0001.wav
  │   ├── 0002.wav
  │   └── ...
  └── stats.json              # {total_samples, total_duration_s, last_updated}
"""

import json
import os
import time
import wave
import numpy as np
from pathlib import Path
from src.config import JARVIS_HOME

VOICE_DATA_DIR = JARVIS_HOME / "voice_data"
AUDIO_DIR = VOICE_DATA_DIR / "audio"
MANIFEST_FILE = VOICE_DATA_DIR / "manifest.jsonl"
STATS_FILE = VOICE_DATA_DIR / "stats.json"

# Minimum quality thresholds for training data
MIN_DURATION_S = 0.5     # Skip very short clips
MAX_DURATION_S = 15.0    # Skip very long clips
MIN_WORDS = 2            # Need at least 2 words
MIN_TEXT_LEN = 5         # Skip very short transcriptions


def _ensure_dirs():
    """Create voice data directories if they don't exist."""
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)


def _get_next_id() -> int:
    """Get the next audio file ID from the manifest."""
    if not MANIFEST_FILE.exists():
        return 1
    count = 0
    with open(MANIFEST_FILE) as f:
        for _ in f:
            count += 1
    return count + 1


def _load_stats() -> dict:
    """Load or create stats."""
    if STATS_FILE.exists():
        with open(STATS_FILE) as f:
            return json.load(f)
    return {"total_samples": 0, "total_duration_s": 0.0, "last_updated": ""}


def _save_stats(stats: dict):
    """Save stats to disk."""
    stats["last_updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(STATS_FILE, "w") as f:
        json.dump(stats, f, indent=2)


def save_training_pair(audio: np.ndarray, text: str, sample_rate: int = 16000) -> bool:
    """Save an audio+text pair for fine-tuning.

    Called from the STT pipeline after a successful transcription.
    Returns True if saved, False if skipped (quality too low).
    """
    # Quality checks
    text = text.strip()
    if not text or len(text) < MIN_TEXT_LEN:
        return False
    if len(text.split()) < MIN_WORDS:
        return False

    duration = len(audio) / sample_rate
    if duration < MIN_DURATION_S or duration > MAX_DURATION_S:
        return False

    _ensure_dirs()

    # Save WAV
    file_id = _get_next_id()
    audio_path = AUDIO_DIR / f"{file_id:05d}.wav"

    int16_audio = (audio * 32767).astype(np.int16)
    with wave.open(str(audio_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(int16_audio.tobytes())

    # Append to manifest
    entry = {
        "audio_path": str(audio_path),
        "text": text,
        "duration_s": round(duration, 2),
        "sample_rate": sample_rate,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(MANIFEST_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")

    # Update stats
    stats = _load_stats()
    stats["total_samples"] += 1
    stats["total_duration_s"] = round(stats["total_duration_s"] + duration, 2)
    _save_stats(stats)

    return True


def get_collection_status() -> dict:
    """Get current data collection status."""
    stats = _load_stats()
    minutes = stats["total_duration_s"] / 60
    ready = minutes >= 30  # 30 min minimum for decent fine-tuning
    return {
        "samples": stats["total_samples"],
        "duration_min": round(minutes, 1),
        "ready_for_training": ready,
        "target_min": 30,
        "progress_pct": min(100, round(minutes / 30 * 100)),
        "last_updated": stats.get("last_updated", "never"),
    }


def load_manifest() -> list[dict]:
    """Load all training entries from the manifest."""
    if not MANIFEST_FILE.exists():
        return []
    entries = []
    with open(MANIFEST_FILE) as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries
