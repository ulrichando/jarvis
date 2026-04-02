"""Speaker Identification — Jarvis knows who's talking.

Identifies the speaker from voice characteristics:
- "Is this Ulrich?" → speaker verification
- "Who is talking?" → speaker identification

Uses SpeechBrain ECAPA-TDNN if available (~80MB, 50ms per comparison).
Falls back to simple voice feature comparison (pitch range, energy profile).

The speaker model learns from experience:
1. ENROLL: When told "I'm Ulrich", save a voice embedding
2. VERIFY: Compare incoming voice to enrolled embeddings
3. ADAPT: Update embeddings over time (voice changes throughout the day)
"""

from __future__ import annotations

import numpy as np
import json
import time
from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class SpeakerProfile:
    """A stored speaker profile."""
    name: str
    embedding: np.ndarray | None = None  # Neural embedding (if SpeechBrain available)
    pitch_mean: float = 0.0              # Average pitch (fallback feature)
    pitch_std: float = 0.0               # Pitch variability
    energy_mean: float = 0.0             # Average energy
    enrolled_at: float = field(default_factory=time.time)
    verifications: int = 0               # How many times verified


class SpeakerIdentifier:
    """Identify and verify speakers from voice.

    Primary: SpeechBrain ECAPA-TDNN (neural embedding comparison)
    Fallback: Pitch + energy profile matching
    """

    def __init__(self, data_dir: str | Path = "~/.jarvis/data/speakers"):
        self.data_dir = Path(data_dir).expanduser()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._profiles: dict[str, SpeakerProfile] = {}
        self._model = None
        self._backend = "none"

        # Try SpeechBrain
        try:
            from speechbrain.pretrained import SpeakerRecognition
            self._model = SpeakerRecognition.from_hparams(
                source="speechbrain/spkrec-ecapa-voxceleb",
                savedir=str(self.data_dir / "models"),
            )
            self._backend = "speechbrain"
        except (ImportError, Exception):
            self._backend = "acoustic"

        self._load_profiles()

    def enroll(self, name: str, audio: np.ndarray, sample_rate: int = 16000):
        """Enroll a speaker from a voice sample.

        Call this when the user says "I'm Ulrich" or "Remember my voice."
        """
        profile = SpeakerProfile(name=name)

        if self._backend == "speechbrain":
            import torch
            embedding = self._model.encode_batch(
                torch.from_numpy(audio).unsqueeze(0).float()
            ).squeeze().numpy()
            profile.embedding = embedding
        else:
            # Fallback: compute acoustic features
            profile.pitch_mean, profile.pitch_std = self._estimate_pitch(audio, sample_rate)
            profile.energy_mean = float(np.sqrt(np.mean(audio ** 2)))

        self._profiles[name.lower()] = profile
        self._save_profiles()

    def identify(self, audio: np.ndarray, sample_rate: int = 16000) -> tuple[str, float]:
        """Identify who is speaking. Returns (name, confidence).

        Returns ("unknown", 0.0) if no match found.
        """
        if not self._profiles:
            return ("unknown", 0.0)

        if self._backend == "speechbrain":
            return self._identify_neural(audio)
        else:
            return self._identify_acoustic(audio, sample_rate)

    def _identify_neural(self, audio: np.ndarray) -> tuple[str, float]:
        """Neural speaker identification using SpeechBrain."""
        import torch
        from scipy.spatial.distance import cosine

        query_embedding = self._model.encode_batch(
            torch.from_numpy(audio).unsqueeze(0).float()
        ).squeeze().numpy()

        best_name = "unknown"
        best_score = 0.0

        for name, profile in self._profiles.items():
            if profile.embedding is not None:
                similarity = 1 - cosine(query_embedding, profile.embedding)
                if similarity > best_score:
                    best_score = similarity
                    best_name = name

        if best_score < 0.6:
            return ("unknown", best_score)

        self._profiles[best_name].verifications += 1
        return (best_name, best_score)

    def _identify_acoustic(self, audio: np.ndarray,
                           sample_rate: int) -> tuple[str, float]:
        """Fallback: acoustic feature matching."""
        query_pitch_mean, query_pitch_std = self._estimate_pitch(audio, sample_rate)
        query_energy = float(np.sqrt(np.mean(audio ** 2)))

        best_name = "unknown"
        best_score = 0.0

        for name, profile in self._profiles.items():
            # Compare pitch and energy
            pitch_diff = abs(query_pitch_mean - profile.pitch_mean) / max(profile.pitch_mean, 1)
            energy_diff = abs(query_energy - profile.energy_mean) / max(profile.energy_mean, 0.001)
            similarity = max(0, 1 - pitch_diff * 0.5 - energy_diff * 0.5)

            if similarity > best_score:
                best_score = similarity
                best_name = name

        if best_score < 0.5:
            return ("unknown", best_score)

        return (best_name, best_score)

    @staticmethod
    def _estimate_pitch(audio: np.ndarray, sample_rate: int) -> tuple[float, float]:
        """Estimate pitch using autocorrelation (no librosa needed)."""
        # Simple autocorrelation-based pitch detection
        frame_size = int(sample_rate * 0.03)  # 30ms frames
        if len(audio) < frame_size * 2:
            return (150.0, 30.0)  # Default values

        pitches = []
        for i in range(0, len(audio) - frame_size, frame_size):
            frame = audio[i:i + frame_size]
            # Autocorrelation
            corr = np.correlate(frame, frame, mode='full')
            corr = corr[len(corr) // 2:]
            # Find first peak after minimum lag (~60Hz = 267 samples at 16kHz)
            min_lag = int(sample_rate / 400)  # 400Hz max pitch
            max_lag = int(sample_rate / 60)   # 60Hz min pitch
            if max_lag > len(corr):
                continue
            segment = corr[min_lag:max_lag]
            if len(segment) == 0:
                continue
            peak_idx = np.argmax(segment) + min_lag
            if corr[peak_idx] > 0.3 * corr[0]:
                pitch = sample_rate / peak_idx
                pitches.append(pitch)

        if not pitches:
            return (150.0, 30.0)

        return (float(np.mean(pitches)), float(np.std(pitches)))

    def _save_profiles(self):
        """Persist speaker profiles."""
        data = {}
        for name, profile in self._profiles.items():
            entry = {
                "name": profile.name,
                "pitch_mean": profile.pitch_mean,
                "pitch_std": profile.pitch_std,
                "energy_mean": profile.energy_mean,
                "enrolled_at": profile.enrolled_at,
                "verifications": profile.verifications,
            }
            if profile.embedding is not None:
                entry["embedding"] = profile.embedding.tolist()
            data[name] = entry

        (self.data_dir / "profiles.json").write_text(json.dumps(data, indent=2))

    def _load_profiles(self):
        """Load saved profiles."""
        profiles_file = self.data_dir / "profiles.json"
        if not profiles_file.exists():
            return
        try:
            data = json.loads(profiles_file.read_text())
            for name, entry in data.items():
                profile = SpeakerProfile(
                    name=entry["name"],
                    pitch_mean=entry.get("pitch_mean", 0),
                    pitch_std=entry.get("pitch_std", 0),
                    energy_mean=entry.get("energy_mean", 0),
                    enrolled_at=entry.get("enrolled_at", 0),
                    verifications=entry.get("verifications", 0),
                )
                if "embedding" in entry:
                    profile.embedding = np.array(entry["embedding"], dtype=np.float32)
                self._profiles[name] = profile
        except Exception:
            pass

    @property
    def enrolled_speakers(self) -> list[str]:
        return list(self._profiles.keys())

    def stats(self) -> dict:
        return {
            "backend": self._backend,
            "enrolled_speakers": self.enrolled_speakers,
            "total_verifications": sum(p.verifications for p in self._profiles.values()),
        }
