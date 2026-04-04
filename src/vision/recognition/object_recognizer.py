"""Object Recognition — shape-based item detection. No ML models.

Detects common objects (phone, cup, keyboard, monitor, book, bottle, hand)
through contour analysis, shape descriptors, and geometric rules.
Learns object positions over time.
"""

import numpy as np
import cv2
from src.vision.recognition.persistence import save_json, load_json


# Shape classification rules: (label, aspect_range, solidity_range, circularity_range, size_range)
_SHAPE_RULES = [
    {
        "label": "monitor",
        "aspect": (0.4, 0.9),
        "solidity": (0.8, 1.0),
        "circularity": (0.3, 0.8),
        "size": (0.08, 0.6),
        "position_y": (0, 0.5),  # top half
    },
    {
        "label": "phone",
        "aspect": (1.4, 2.5),
        "solidity": (0.8, 1.0),
        "circularity": (0.2, 0.7),
        "size": (0.005, 0.06),
    },
    {
        "label": "book",
        "aspect": (1.0, 2.0),
        "solidity": (0.85, 1.0),
        "circularity": (0.3, 0.8),
        "size": (0.02, 0.15),
    },
    {
        "label": "cup",
        "aspect": (0.8, 1.6),
        "solidity": (0.7, 0.95),
        "circularity": (0.4, 0.9),
        "size": (0.005, 0.04),
    },
    {
        "label": "bottle",
        "aspect": (2.0, 5.0),
        "solidity": (0.7, 1.0),
        "circularity": (0.1, 0.5),
        "size": (0.005, 0.06),
    },
    {
        "label": "keyboard",
        "aspect": (0.15, 0.45),
        "solidity": (0.6, 0.95),
        "circularity": (0.1, 0.5),
        "size": (0.03, 0.2),
        "position_y": (0.5, 1.0),  # bottom half
    },
]


class DetectedObject:
    __slots__ = ('label', 'confidence', 'bbox', 'color', 'shape_desc', 'stable_frames')

    def __init__(self, label: str, confidence: float, bbox: tuple, color: str = ""):
        self.label = label
        self.confidence = confidence
        self.bbox = bbox
        self.color = color
        self.shape_desc: np.ndarray = np.zeros(7)
        self.stable_frames: int = 0


class ObjectRecognizer:
    """Detects objects through contour shape analysis."""

    def __init__(self):
        self.objects: list[DetectedObject] = []
        self._known_positions: list[dict] = []  # learned stable objects
        self._save_counter: int = 0
        self._load()

    def process(self, frame: np.ndarray, gray: np.ndarray, hsv: np.ndarray,
                env=None, persons: list = None, **kwargs) -> dict:
        """Detect objects via background subtraction + contour analysis."""
        h, w = frame.shape[:2]
        frame_area = h * w

        # Get foreground mask
        fg_mask = self._get_foreground(gray, env, h, w)
        if fg_mask is None:
            return self._result()

        # Exclude person regions from object detection
        if persons:
            for p in persons:
                px, py, pw, ph = p.bbox
                # Expand person bbox by 20%
                ex = max(0, px - pw // 5)
                ey = max(0, py - ph // 5)
                ew = min(w, px + pw + pw // 5)
                eh = min(h, py + ph + ph // 5)
                fg_mask[ey:eh, ex:ew] = 0

        # Find contours
        contours, _ = cv2.findContours(fg_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        self.objects = []
        for contour in contours:
            area = cv2.contourArea(contour)
            rel_area = area / frame_area
            if rel_area < 0.003 or rel_area > 0.5:
                continue

            x, y, bw, bh = cv2.boundingRect(contour)
            aspect = bh / max(bw, 1)
            perimeter = cv2.arcLength(contour, True)
            circularity = (4 * np.pi * area) / (perimeter * perimeter + 1e-6)

            hull = cv2.convexHull(contour)
            hull_area = cv2.contourArea(hull)
            solidity = area / (hull_area + 1e-6)

            # Classify by shape rules
            label, confidence = self._classify(
                aspect, solidity, circularity, rel_area,
                y / h, (y + bh) / h
            )

            # Get dominant color
            roi_hsv = hsv[y:y+bh, x:x+bw]
            color = self._dominant_color(roi_hsv)

            obj = DetectedObject(label, confidence, (x, y, bw, bh), color)
            self.objects.append(obj)

        # Match with known positions (persistence)
        self._match_known_positions()

        # Periodic save
        self._save_counter += 1
        if self._save_counter % 20 == 0:
            self._save()

        return self._result()

    def _get_foreground(self, gray: np.ndarray, env, h: int, w: int) -> np.ndarray | None:
        """Get foreground mask via background subtraction."""
        if env is None or env.background is None:
            # Fallback: use adaptive thresholding
            blur = cv2.GaussianBlur(gray, (11, 11), 0)
            _, thresh = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            # Morphological cleanup
            kernel = np.ones((5, 5), np.uint8)
            return cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel)

        # Background subtraction
        bg_gray = np.mean(env.background, axis=2).astype(np.uint8)
        diff = cv2.absdiff(gray, bg_gray)

        # Adaptive threshold based on background variance
        if env.bg_variance is not None:
            threshold = np.clip(env.bg_variance * 2 + 15, 15, 60).astype(np.uint8)
            fg = (diff > threshold).astype(np.uint8) * 255
        else:
            _, fg = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)

        # Cleanup
        kernel = np.ones((5, 5), np.uint8)
        fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, kernel)
        fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, kernel)
        return fg

    def _classify(self, aspect: float, solidity: float, circularity: float,
                  rel_area: float, y_top: float, y_bottom: float) -> tuple[str, float]:
        """Classify a contour using geometric rules."""
        best_label = "object"
        best_score = 0.0

        for rule in _SHAPE_RULES:
            score = 0.0
            checks = 0

            # Aspect ratio
            lo, hi = rule["aspect"]
            if lo <= aspect <= hi:
                score += 1.0
            elif abs(aspect - lo) < 0.3 or abs(aspect - hi) < 0.3:
                score += 0.3
            checks += 1

            # Solidity
            lo, hi = rule["solidity"]
            if lo <= solidity <= hi:
                score += 1.0
            checks += 1

            # Circularity
            lo, hi = rule["circularity"]
            if lo <= circularity <= hi:
                score += 1.0
            checks += 1

            # Size
            lo, hi = rule["size"]
            if lo <= rel_area <= hi:
                score += 1.0
            checks += 1

            # Position constraint (optional)
            if "position_y" in rule:
                lo, hi = rule["position_y"]
                mid_y = (y_top + y_bottom) / 2
                if lo <= mid_y <= hi:
                    score += 0.5
                checks += 0.5

            confidence = score / checks if checks > 0 else 0
            if confidence > best_score:
                best_score = confidence
                best_label = rule["label"]

        # Minimum threshold
        if best_score < 0.5:
            best_label = "object"
            best_score = 0.3

        return best_label, round(best_score, 2)

    @staticmethod
    def _dominant_color(roi_hsv: np.ndarray) -> str:
        """Get dominant color name from an HSV ROI."""
        if roi_hsv.size < 30:
            return "unknown"
        mean_h = float(np.mean(roi_hsv[:, :, 0]))
        mean_s = float(np.mean(roi_hsv[:, :, 1]))
        mean_v = float(np.mean(roi_hsv[:, :, 2]))

        if mean_s < 40:
            return "black" if mean_v < 80 else ("gray" if mean_v < 180 else "white")
        if mean_v < 50:
            return "black"

        hue_names = ["red", "orange", "yellow", "green", "cyan", "blue", "purple", "red"]
        idx = int(mean_h / 23) % 8
        return hue_names[idx]

    def _match_known_positions(self):
        """Match detected objects to previously seen stable objects."""
        for obj in self.objects:
            ox, oy, ow, oh = obj.bbox
            center = (ox + ow / 2, oy + oh / 2)

            for known in self._known_positions:
                kx, ky = known["center"]
                dist = np.sqrt((center[0] - kx) ** 2 + (center[1] - ky) ** 2)
                if dist < 40 and known["label"] == obj.label:
                    obj.stable_frames = known["count"]
                    known["count"] += 1
                    known["center"] = center  # update position
                    break
            else:
                # New object position
                self._known_positions.append({
                    "label": obj.label,
                    "center": center,
                    "count": 1,
                })

        # Prune positions not seen recently (keep top 20)
        self._known_positions.sort(key=lambda k: k["count"], reverse=True)
        self._known_positions = self._known_positions[:20]

    def _save(self):
        if not self._known_positions:
            return
        data = {
            "positions": [
                {"label": k["label"], "center": list(k["center"]), "count": k["count"]}
                for k in self._known_positions[:20]
            ]
        }
        save_json("objects.json", data)

    def _load(self):
        data = load_json("objects.json")
        if not data:
            return
        for p in data.get("positions", []):
            self._known_positions.append({
                "label": p["label"],
                "center": tuple(p["center"]),
                "count": p.get("count", 1),
            })

    def _result(self) -> dict:
        obj_list = [
            {
                "label": o.label,
                "position": self._position_name(o.bbox),
                "confidence": o.confidence,
                "color": o.color,
            }
            for o in self.objects
        ]

        # Desk layout summary
        layout_parts = []
        for o in sorted(self.objects, key=lambda x: x.bbox[0]):
            if o.confidence >= 0.5:
                pos = self._position_name(o.bbox)
                layout_parts.append(f"{o.color} {o.label} {pos}")

        return {
            "objects": obj_list,
            "object_count": len(self.objects),
            "desk_layout": ", ".join(layout_parts) if layout_parts else None,
        }

    @staticmethod
    def _position_name(bbox: tuple) -> str:
        x, y, w, h = bbox
        cx = x + w / 2
        if cx < 107:
            return "left"
        elif cx > 213:
            return "right"
        return "center"
