"""Gesture Recognition — hand detection and body language analysis.

Detects hands via skin blobs (excluding face), counts fingers via
convex hull defects, classifies gestures and body language.
"""

import numpy as np
import cv2
from collections import deque


class GestureRecognizer:
    """Detects hand gestures and body language from skin blobs + motion."""

    def __init__(self):
        self.hands: list[dict] = []
        self.body_language: str = "neutral"
        self.gesture_history: deque[str] = deque(maxlen=30)
        self._prev_face_center: tuple | None = None
        self._face_y_history: deque[float] = deque(maxlen=10)
        self._face_x_history: deque[float] = deque(maxlen=10)

    def process(self, frame: np.ndarray, gray: np.ndarray,
                skin_mask: np.ndarray, persons: list = None,
                motion_grid: np.ndarray = None, **kwargs) -> dict:
        """Detect hands and analyze body language."""
        h, w = frame.shape[:2]
        self.hands = []

        # Create a mask excluding face regions
        hand_mask = skin_mask.copy()
        face_bboxes = []
        if persons:
            for p in persons:
                x, y, bw, bh = p.bbox
                # Expand face region to exclude neck/shoulders
                ex = max(0, x - bw // 4)
                ey = max(0, y - bh // 4)
                ew = min(w, x + bw + bw // 4)
                eh = min(h, y + bh + bh // 2)  # extend down for neck
                hand_mask[ey:eh, ex:ew] = 0
                face_bboxes.append((x, y, bw, bh))

        # Find hand candidates
        contours, _ = cv2.findContours(hand_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        for contour in contours:
            area = cv2.contourArea(contour)
            rel_area = area / (h * w)
            if rel_area < 0.003 or rel_area > 0.15:
                continue

            x, y, bw, bh = cv2.boundingRect(contour)
            aspect = bh / max(bw, 1)

            # Hands are irregular (low solidity compared to face)
            hull = cv2.convexHull(contour)
            hull_area = cv2.contourArea(hull)
            solidity = area / (hull_area + 1e-6)

            # Filter: hands have lower solidity than face blobs
            if solidity > 0.9:
                continue

            # Count fingers via convex hull defects
            fingers, defect_count = self._count_fingers(contour)

            # Classify gesture
            gesture = self._classify_gesture(fingers, defect_count, aspect, solidity)

            # Determine hand side
            cx = x + bw / 2
            side = "left" if cx < w / 2 else "right"

            self.hands.append({
                "bbox": (x, y, bw, bh),
                "fingers": fingers,
                "gesture": gesture,
                "side": side,
                "confidence": min(0.9, 0.4 + defect_count * 0.1 + (1 - solidity) * 0.3),
            })

            if gesture != "none":
                self.gesture_history.append(gesture)

        # Body language analysis
        if persons:
            self._analyze_body_language(persons, h, w, motion_grid)

        return {
            "gestures": [
                {"type": hand["gesture"], "hand": hand["side"],
                 "fingers": hand["fingers"], "confidence": round(hand["confidence"], 2)}
                for hand in self.hands
            ],
            "body_language": self.body_language,
            "hands_visible": len(self.hands),
        }

    def _count_fingers(self, contour: np.ndarray) -> tuple[int, int]:
        """Count fingers using convex hull defects."""
        if len(contour) < 5:
            return 0, 0

        hull_indices = cv2.convexHull(contour, returnPoints=False)
        if hull_indices is None or len(hull_indices) < 3:
            return 0, 0

        try:
            defects = cv2.convexityDefects(contour, hull_indices)
        except cv2.error:
            return 0, 0

        if defects is None:
            return 0, 0

        # Count significant defects (inter-finger gaps)
        finger_defects = 0
        for d in defects:
            s, e, f, depth = d[0]
            if depth > 3000:  # depth threshold (in 1/256 pixels)
                # Angle check: finger gaps have angle < 90°
                start = contour[s][0]
                end = contour[e][0]
                far = contour[f][0]
                a = np.linalg.norm(start - far)
                b = np.linalg.norm(end - far)
                c = np.linalg.norm(start - end)
                if a > 0 and b > 0:
                    angle = np.arccos(np.clip((a*a + b*b - c*c) / (2*a*b + 1e-6), -1, 1))
                    if angle < np.pi * 0.55:  # < ~100°
                        finger_defects += 1

        # Fingers = defects + 1 (the gaps between fingers)
        fingers = min(5, finger_defects + 1) if finger_defects > 0 else 0
        return fingers, finger_defects

    @staticmethod
    def _classify_gesture(fingers: int, defects: int, aspect: float, solidity: float) -> str:
        """Classify hand gesture from finger count and shape."""
        if fingers == 0 and solidity > 0.75:
            return "fist"
        if fingers == 1 and defects == 1:
            return "pointing"
        if fingers == 2 and defects >= 1:
            return "peace"
        if fingers >= 4:
            return "open_palm"
        if defects == 0 and aspect > 2.0:
            return "thumbs_up"

        # Check for wave from history (handled in body language)
        return "none"

    def _analyze_body_language(self, persons: list, h: int, w: int,
                               motion_grid: np.ndarray = None):
        """Analyze body language from face position changes and motion."""
        if not persons:
            self.body_language = "neutral"
            return

        p = persons[0]
        fx, fy, fw, fh = p.bbox
        face_cy = fy + fh / 2
        face_cx = fx + fw / 2

        self._face_y_history.append(face_cy)
        self._face_x_history.append(face_cx)

        if len(self._face_y_history) < 4:
            self.body_language = "neutral"
            return

        y_vals = list(self._face_y_history)
        x_vals = list(self._face_x_history)

        # Leaning detection: face moving consistently in one direction
        y_trend = y_vals[-1] - y_vals[-3]
        x_trend = x_vals[-1] - x_vals[-3]

        # Nodding: oscillation in Y
        y_diffs = [y_vals[i+1] - y_vals[i] for i in range(len(y_vals)-1)]
        sign_changes_y = sum(1 for i in range(len(y_diffs)-1)
                            if y_diffs[i] * y_diffs[i+1] < 0)

        # Head shake: oscillation in X
        x_diffs = [x_vals[i+1] - x_vals[i] for i in range(len(x_vals)-1)]
        sign_changes_x = sum(1 for i in range(len(x_diffs)-1)
                            if x_diffs[i] * x_diffs[i+1] < 0)

        if sign_changes_y >= 3 and max(abs(d) for d in y_diffs) > 3:
            self.body_language = "nodding"
        elif sign_changes_x >= 3 and max(abs(d) for d in x_diffs) > 3:
            self.body_language = "shaking_head"
        elif y_trend < -8:
            self.body_language = "leaning_forward"
        elif y_trend > 8:
            self.body_language = "leaning_back"
        elif abs(x_trend) > 10:
            self.body_language = "shifting"
        else:
            self.body_language = "still"

        # Wave detection: hand visible + horizontal oscillation
        if self.hands:
            recent_gestures = list(self.gesture_history)[-5:]
            if recent_gestures.count("open_palm") >= 2:
                self.body_language = "waving"
