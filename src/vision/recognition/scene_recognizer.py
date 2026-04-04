"""Scene Understanding — room type, setup, and context classification.

Analyzes spatial layout, color palette, and detected objects to determine
what kind of scene JARVIS is looking at.
"""

import numpy as np
import cv2


class SceneRecognizer:
    """Classifies the scene type from spatial layout and context."""

    def __init__(self):
        self.scene_type: str = "unknown"
        self.scene_details: str = ""
        self.scene_confidence: float = 0.0
        self._home_scene: str | None = None
        self._scene_stability: int = 0
        self._last_scene: str = "unknown"

    def process(self, frame: np.ndarray, gray: np.ndarray, hsv: np.ndarray,
                color_result: dict = None, object_result: dict = None,
                persons: list = None, **kwargs) -> dict:
        """Classify the scene from spatial layout + context."""
        h, w = gray.shape[:2]

        # Spatial band analysis: top / middle / bottom thirds
        third = h // 3
        bands = [gray[:third, :], gray[third:2*third, :], gray[2*third:, :]]

        band_features = np.zeros((3, 4))
        for i, band in enumerate(bands):
            band_features[i, 0] = float(np.mean(band))                    # brightness
            band_features[i, 1] = float(np.std(band))                     # contrast
            edges = cv2.Sobel(band, cv2.CV_32F, 1, 0, ksize=3)
            band_features[i, 2] = float(np.mean(np.abs(edges)))           # edge density
            band_features[i, 3] = float(np.mean(hsv[:third if i == 0 else (third if i == 1 else 0):
                                                      (third if i == 0 else (2*third if i == 1 else h)),
                                                      :, 1]))             # color saturation

        # Collect evidence for each scene type
        scores = {
            "desk_setup": 0.0,
            "indoor_room": 0.0,
            "outdoor": 0.0,
            "dark_room": 0.0,
            "meeting": 0.0,
        }

        # Objects present
        objects = (object_result or {}).get("objects", [])
        obj_labels = [o["label"] for o in objects]

        # Color info
        color_mood = (color_result or {}).get("color_mood", "neutral")
        dominant_colors = (color_result or {}).get("dominant_colors", [])

        person_count = len(persons) if persons else 0

        # ── Desk setup evidence ──
        if "keyboard" in obj_labels:
            scores["desk_setup"] += 0.3
        if "monitor" in obj_labels:
            scores["desk_setup"] += 0.3
        if "phone" in obj_labels:
            scores["desk_setup"] += 0.1
        if person_count == 1:
            scores["desk_setup"] += 0.2
        # Bottom band has more horizontal edges (desk surface)
        if band_features[2, 2] > band_features[0, 2]:
            scores["desk_setup"] += 0.1

        # ── Indoor room evidence ──
        mean_brightness = float(np.mean(gray))
        if 80 < mean_brightness < 200:
            scores["indoor_room"] += 0.2
        if color_mood == "warm":
            scores["indoor_room"] += 0.15
        if band_features[0, 1] < 40:  # top band low contrast = ceiling
            scores["indoor_room"] += 0.15
        scores["indoor_room"] += 0.1  # baseline — most scenes are indoor

        # ── Outdoor evidence ──
        if mean_brightness > 170:
            scores["outdoor"] += 0.2
        if band_features[0, 0] > 180:  # bright top = sky
            scores["outdoor"] += 0.3
        if any("blue" in c for c in dominant_colors):
            scores["outdoor"] += 0.1
        if band_features[0, 3] > 60:  # high saturation at top
            scores["outdoor"] += 0.1

        # ── Dark room evidence ──
        if mean_brightness < 60:
            scores["dark_room"] += 0.5
        if band_features[0, 0] < 40 and band_features[2, 0] < 40:
            scores["dark_room"] += 0.3

        # ── Meeting evidence ──
        if person_count >= 2:
            scores["meeting"] += 0.5
        elif person_count == 1:
            # Single person, stable position, looking at camera = video call?
            if persons and hasattr(persons[0], 'gaze') and persons[0].gaze == "at_camera":
                scores["meeting"] += 0.1

        # Pick winner
        best_scene = max(scores, key=scores.get)
        best_score = scores[best_scene]

        if best_score < 0.2:
            best_scene = "unknown"

        self.scene_type = best_scene
        self.scene_confidence = min(1.0, best_score)

        # Build details string
        details_parts = [best_scene.replace("_", " ")]
        if objects:
            named = [o["label"] for o in objects if o["confidence"] >= 0.5]
            if named:
                details_parts.append(f"with {', '.join(named[:4])}")
        if color_mood != "neutral":
            details_parts.append(f"{color_mood} lighting")
        self.scene_details = "; ".join(details_parts)

        # Scene stability tracking
        if best_scene == self._last_scene:
            self._scene_stability += 1
            if self._scene_stability > 50 and self._home_scene is None:
                self._home_scene = best_scene
                print(f"[CORTEX-SCENE] Home scene learned: {best_scene}")
        else:
            self._scene_stability = 0
        self._last_scene = best_scene

        return {
            "scene_type": self.scene_type,
            "scene_details": self.scene_details,
            "scene_confidence": round(self.scene_confidence, 2),
        }
