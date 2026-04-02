"""Color Recognition — dominant color extraction, naming, and tracking.

Pure numpy + cv2.cvtColor. No ML models.
Identifies dominant colors, tracks color changes, names colors in English.
"""

import numpy as np
import cv2

# Hue bin → color name (12 bins of 15° each in OpenCV's 0-180 H range)
_HUE_NAMES = [
    "red", "red-orange", "orange", "yellow",
    "yellow-green", "green", "green-cyan", "cyan",
    "blue", "blue-purple", "purple", "magenta",
]

# Saturation levels
_SAT_NAMES = ["gray", "muted", "vivid"]

# Value/brightness levels
_VAL_NAMES = ["dark", "", "bright"]


def _color_name(h_bin: int, s_bin: int, v_bin: int) -> str:
    """Map HSV bin indices to a human-readable color name."""
    if s_bin == 0:
        # Low saturation = grayscale
        return ["black", "gray", "white"][v_bin]
    if v_bin == 0:
        return f"dark {_HUE_NAMES[h_bin]}"

    parts = []
    if v_bin == 2 and s_bin >= 1:
        parts.append("bright")
    elif s_bin == 1:
        parts.append("muted")
    parts.append(_HUE_NAMES[h_bin])
    return " ".join(parts)


class ColorRecognizer:
    """Identifies dominant colors in the scene and tracks changes."""

    def __init__(self):
        self.dominant_colors: list[str] = []
        self.color_mood: str = "neutral"
        self.color_change: bool = False
        self._scene_hist: np.ndarray = np.zeros(108)  # 12 H × 3 S × 3 V
        self._hist_baseline: np.ndarray | None = None
        self._frame_count: int = 0
        self._clothing_palettes: dict[str, np.ndarray] = {}

    def process(self, frame: np.ndarray, hsv: np.ndarray,
                persons: list = None, **kwargs) -> dict:
        """Analyze colors in the frame."""
        # Downscale for speed
        small = cv2.resize(hsv, (40, 30), interpolation=cv2.INTER_AREA)

        # Quantize into 108-bin histogram
        h_bins = np.clip(small[:, :, 0].ravel() // 15, 0, 11).astype(int)
        s_bins = np.clip(small[:, :, 1].ravel() // 86, 0, 2).astype(int)
        v_bins = np.clip(small[:, :, 2].ravel() // 86, 0, 2).astype(int)

        hist = np.zeros(108)
        indices = h_bins * 9 + s_bins * 3 + v_bins
        for idx in indices:
            hist[idx] += 1

        total = np.sum(hist)
        if total > 0:
            hist /= total

        # Top 3 dominant colors
        top_indices = np.argsort(hist)[-3:][::-1]
        self.dominant_colors = []
        for idx in top_indices:
            if hist[idx] < 0.02:
                continue
            h_bin = idx // 9
            s_bin = (idx % 9) // 3
            v_bin = idx % 3
            self.dominant_colors.append(_color_name(h_bin, s_bin, v_bin))

        # Color mood from saturation/warmth
        mean_s = float(np.mean(small[:, :, 1]))
        mean_h = float(np.mean(small[:, :, 0]))
        if mean_s < 40:
            self.color_mood = "neutral"
        elif mean_s > 120:
            self.color_mood = "vivid"
        elif mean_h < 30 or mean_h > 150:
            self.color_mood = "warm"
        elif 75 < mean_h < 135:
            self.color_mood = "cool"
        else:
            self.color_mood = "neutral"

        # Color change detection
        self.color_change = False
        if self._hist_baseline is not None:
            # Chi-squared distance
            diff = (hist - self._hist_baseline) ** 2
            denom = hist + self._hist_baseline + 1e-8
            chi_sq = float(np.sum(diff / denom))
            self.color_change = chi_sq > 0.3

        # Update baseline (slow adaptation)
        if self._hist_baseline is None:
            self._hist_baseline = hist.copy()
        else:
            self._hist_baseline = self._hist_baseline * 0.97 + hist * 0.03

        self._scene_hist = hist
        self._frame_count += 1

        return {
            "dominant_colors": self.dominant_colors[:3],
            "color_mood": self.color_mood,
            "color_change": self.color_change,
        }
