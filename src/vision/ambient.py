"""JARVIS Cortical Vision — biologically-inspired perception engine.

Replaces traditional CV classifiers (Haar cascades, pretrained models) with
a multi-layered signal fusion system built from pure math and numpy.

6 Cortical Layers:
  1. Chromatic Skin Map — detect skin in YCrCb/HSV (all skin tones, ~0.1ms)
  2. Bilateral Symmetry — faces are symmetric, hands aren't (~0.3ms)
  3. Edge Structure — eye/brow/nose/mouth signature from gradients (~0.3ms)
  4. Temporal Motion Field — micro-motion, breathing, activity (~0.2ms)
  5. Appearance Fingerprint — identity through color/geometry (~0.2ms)
  6. Spatial Attention — gaze direction from edge distribution (~0.05ms)

Total: ~3.5ms per frame. No pretrained models. Learns your environment.
"""

import numpy as np
import time
from collections import deque

import cv2  # used only for: imdecode, cvtColor, connectedComponentsWithStats, Sobel

from src.vision.recognition.engine import RecognitionEngine


# ── Data Structures ────────────────────────────────────────────────

class FaceLandmarks:
    """Detected facial feature positions (relative to face bbox)."""
    __slots__ = (
        'left_eye', 'right_eye', 'nose', 'mouth', 'left_ear', 'right_ear',
        'confidence', 'eye_distance', 'face_tilt',
    )

    def __init__(self):
        self.left_eye: tuple | None = None    # (x, y) relative to face bbox
        self.right_eye: tuple | None = None
        self.nose: tuple | None = None
        self.mouth: tuple | None = None        # center of mouth region
        self.left_ear: tuple | None = None
        self.right_ear: tuple | None = None
        self.confidence: float = 0.0
        self.eye_distance: float = 0.0         # pixels between eyes
        self.face_tilt: float = 0.0            # degrees, 0 = level

    def to_dict(self, offset_x: int = 0, offset_y: int = 0) -> dict:
        """Convert to dict with absolute frame coordinates."""
        def _abs(pt):
            if pt is None:
                return None
            return (int(pt[0] + offset_x), int(pt[1] + offset_y))
        return {
            "left_eye": _abs(self.left_eye),
            "right_eye": _abs(self.right_eye),
            "nose": _abs(self.nose),
            "mouth": _abs(self.mouth),
            "left_ear": _abs(self.left_ear),
            "right_ear": _abs(self.right_ear),
            "eye_distance": round(self.eye_distance, 1),
            "face_tilt": round(self.face_tilt, 1),
            "confidence": round(self.confidence, 2),
        }


class PersonSnapshot:
    """A single observation of a person in one frame."""
    __slots__ = (
        'timestamp', 'bbox', 'skin_area', 'symmetry', 'face_structure',
        'appearance', 'motion_sig', 'gaze', 'expression', 'micro_motion',
        'confidence', 'landmarks',
    )

    def __init__(self):
        self.timestamp: float = 0
        self.bbox: tuple = (0, 0, 0, 0)  # x, y, w, h
        self.skin_area: float = 0
        self.symmetry: float = 0
        self.face_structure: np.ndarray = np.zeros(3)
        self.appearance: np.ndarray = np.zeros(71)
        self.motion_sig: np.ndarray = np.zeros(48)
        self.gaze: str = "unknown"
        self.expression: str = "neutral"
        self.micro_motion: float = 0
        self.landmarks: FaceLandmarks = FaceLandmarks()
        self.confidence: float = 0


class IdentityProfile:
    """Learned identity — built from repeated observations."""
    __slots__ = (
        'id', 'label', 'centroid', 'variance', 'motion_style',
        'count', 'first_seen', 'last_seen', 'total_time',
        'typical_position', 'typical_gaze',
    )

    def __init__(self, identity_id: str):
        self.id = identity_id
        self.label = "unknown"
        self.centroid: np.ndarray = np.zeros(71)
        self.variance: np.ndarray = np.ones(71)
        self.motion_style: np.ndarray = np.zeros(48)
        self.count: int = 0
        self.first_seen: float = 0
        self.last_seen: float = 0
        self.total_time: float = 0
        self.typical_position: str = "center"
        self.typical_gaze: str = "at_camera"


class EnvironmentModel:
    """Adaptive model of the room/scene."""

    def __init__(self, h: int = 240, w: int = 320):
        self.background: np.ndarray | None = None  # running average (float32)
        self.bg_variance: np.ndarray | None = None
        self.lighting_baseline: float = 128.0
        self.lighting_variance: float = 10.0
        self.edge_baseline: float = 0.1
        self.empty_frames: int = 0
        self.calibrated: bool = False
        self.skin_cr_range = (133, 173)  # adaptive
        self.skin_cb_range = (77, 127)   # adaptive


# ── Cortical Viewer ────────────────────────────────────────────────

class CorticalViewer:
    """Multi-layered perception engine. Drop-in replacement for AmbientViewer."""

    def __init__(
        self,
        motion_threshold: float = 5.0,
        face_interval: float = 2.0,
        scene_interval: float = 5.0,
        change_threshold: float = 15.0,
    ):
        self.motion_threshold = motion_threshold
        self.face_interval = face_interval
        self.scene_interval = scene_interval
        self.change_threshold = change_threshold

        # ── Frame state ──
        self._prev_gray: np.ndarray | None = None
        self._prev_frame: np.ndarray | None = None
        self._frame_count: int = 0
        self._last_face_check: float = 0
        self._last_scene_check: float = 0
        self._last_identity_check: float = 0

        # ── Environment ──
        self.env = EnvironmentModel()

        # ── Current perception ──
        self.persons: list[PersonSnapshot] = []
        self.faces: list[dict] = []  # backward compat
        self.motion_level: float = 0.0
        self.brightness: str = "unknown"
        self.scene_description: str = ""
        self.person_present: bool = False
        self.person_smiling: bool = False
        self.last_change_time: float = 0

        # ── Temporal buffers ──
        self._motion_grid_history: deque[np.ndarray] = deque(maxlen=15)
        self._motion_history: deque[float] = deque(maxlen=30)
        self._face_history: deque[bool] = deque(maxlen=10)
        self._snapshot_history: deque[PersonSnapshot] = deque(maxlen=30)
        self._presence_history: deque[tuple[float, bool]] = deque(maxlen=60)

        # ── Hysteresis ──
        self._consecutive_present: int = 0
        self._consecutive_absent: int = 0
        self._confirmed_present: bool = False

        # ── Identity bank ──
        self._identities: dict[str, IdentityProfile] = {}
        self._next_identity_id: int = 0
        self._current_identity: str | None = None

        # ── Activity state ──
        self.activity: str = "unknown"
        self.engagement: float = 0.0
        self.mood: str = "unknown"
        self.attention_on_screen: bool = False

        # ── Recognition engine ──
        self.recognition = RecognitionEngine()

        # ── Haar cascades as OPTIONAL fallback (loaded lazily, only for smile) ──
        self._face_cascade = None
        self._smile_cascade = None
        self._eye_cascade = None

    # ══════════════════════════════════════════════════════════════════
    # PUBLIC API (backward-compatible with AmbientViewer)
    # ══════════════════════════════════════════════════════════════════

    def feed(self, frame_bytes: bytes) -> dict | None:
        """Feed a JPEG frame. Returns event dict if something notable happened."""
        frame = self._decode(frame_bytes)
        if frame is None:
            return None

        self._frame_count += 1
        now = time.time()
        h, w = frame.shape[:2]

        # Color space conversions (done once, reused by all layers)
        ycrcb = cv2.cvtColor(frame, cv2.COLOR_BGR2YCrCb)
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        gray = ycrcb[:, :, 0]  # Y channel = luminance (free grayscale)
        gray_blur = cv2.GaussianBlur(gray, (15, 15), 0)

        event = None

        # ── Layer 0: Brightness ──
        mean_b = float(np.mean(gray))
        self.brightness = (
            "very dark" if mean_b < 50 else
            "dim" if mean_b < 100 else
            "well lit" if mean_b < 170 else
            "very bright"
        )

        # ── Layer 4: Motion Field ──
        motion_grid = self._compute_motion_field(gray_blur, h, w)

        # ── Global motion level (backward compat) ──
        if self._prev_gray is not None:
            diff = cv2.absdiff(self._prev_gray, gray_blur)
            self.motion_level = float(np.count_nonzero(diff > 25) / diff.size) * 100
            self._motion_history.append(self.motion_level)
        self._prev_gray = gray_blur

        # ── Person detection (throttled) ──
        if now - self._last_face_check >= self.face_interval:
            self._last_face_check = now
            prev_present = self._confirmed_present

            # Layer 1: Skin detection
            skin_mask = self._detect_skin(ycrcb)
            self._last_skin_mask = skin_mask  # store for recognition engine
            blobs = self._find_skin_blobs(skin_mask, h, w)

            # Layers 2+3: Validate blobs → persons
            self.persons = []
            for blob in blobs:
                snap = self._analyze_blob(blob, frame, gray, hsv, skin_mask, motion_grid, now)
                if snap and snap.confidence >= 0.4:
                    self.persons.append(snap)

            # Hysteresis: smooth presence detection
            raw_present = len(self.persons) > 0
            if raw_present:
                self._consecutive_present += 1
                self._consecutive_absent = 0
            else:
                self._consecutive_absent += 1
                self._consecutive_present = 0

            if not self._confirmed_present and self._consecutive_present >= 2:
                self._confirmed_present = True
            elif self._confirmed_present and self._consecutive_absent >= 3:
                self._confirmed_present = False

            self.person_present = self._confirmed_present
            self._face_history.append(self.person_present)
            self._presence_history.append((now, self.person_present))

            # Build backward-compat faces list
            self.faces = []
            self.person_smiling = False
            for p in self.persons:
                face = {
                    "position": self._bbox_position(p.bbox, w),
                    "size": self._bbox_distance(p.bbox, w),
                    "smiling": p.expression == "smiling",
                    "gaze": p.gaze,
                    "confidence": round(p.confidence, 2),
                    "landmarks": p.landmarks.to_dict(p.bbox[0], p.bbox[1]),
                }
                if p.expression == "smiling":
                    self.person_smiling = True
                self.faces.append(face)

            # Layer 5: Identity matching (every 5s)
            if self.person_present and now - self._last_identity_check >= 5.0:
                self._last_identity_check = now
                for p in self.persons:
                    self._match_identity(p, now)

            # Layer 4 continued: Activity inference
            if self.person_present:
                self._infer_activity()

            # Store snapshots
            for p in self.persons:
                self._snapshot_history.append(p)

            # Events
            if self._confirmed_present and not prev_present:
                identity = self._current_identity or "unknown"
                event = {
                    "type": "person_appeared",
                    "faces": self.faces,
                    "smiling": self.person_smiling,
                    "identity": identity,
                    "gaze": self.persons[0].gaze if self.persons else "unknown",
                }
            elif not self._confirmed_present and prev_present:
                event = {
                    "type": "person_left",
                    "identity": self._current_identity or "unknown",
                }
                self._current_identity = None

        # ── Recognition engine (runs on same cadence as face detection) ──
        skin = getattr(self, '_last_skin_mask', None)
        if skin is not None:
            try:
                self.recognition.process(
                    frame=frame, gray=gray, hsv=hsv, ycrcb=ycrcb,
                    skin_mask=skin, motion_grid=motion_grid,
                    persons=self.persons, env=self.env,
                )
                # Use face recognizer's identity if available
                face_id = self.recognition.face.current_identity
                if face_id and self.recognition.face.current_confidence > 0.5:
                    self._current_identity = face_id
            except Exception as e:
                if self._frame_count <= 3:
                    print(f"[CORTEX] Recognition error: {e}")

        # ── Scene analysis (throttled) ──
        if now - self._last_scene_check >= self.scene_interval:
            self._last_scene_check = now
            self._analyze_scene(gray)
            self._update_environment(frame, gray)

        self._prev_frame = frame
        return event

    def get_awareness(self) -> dict:
        """Return current visual awareness for JARVIS's context."""
        rec = self.recognition.results
        parts = []

        if self.person_present:
            # Use face recognizer's label if available
            identity_label = rec.get("face_identity", "unknown")
            if identity_label in ("unknown", None):
                identity_label = "Someone"
            else:
                identity_label = identity_label.title()

            n = len(self.persons)
            if n == 1:
                p = self.persons[0]
                parts.append(f"{identity_label} is present")
                if p.gaze == "at_camera":
                    parts.append("looking at screen")
                elif p.gaze != "unknown":
                    parts.append(f"looking {p.gaze}")
                if p.expression == "smiling":
                    parts.append("smiling")
            else:
                parts.append(f"{n} people visible")
                if self.person_smiling:
                    parts.append("someone smiling")
        else:
            parts.append("no one visible")

        parts.append(f"lighting: {self.brightness}")

        if self.activity not in ("unknown", "away"):
            parts.append(f"activity: {self.activity}")

        if self.motion_level > 20:
            parts.append("significant movement")
        elif self.motion_level > 5:
            parts.append("some movement")

        # Scene from recognition engine
        scene_details = rec.get("scene_details", "")
        if scene_details:
            parts.append(scene_details)
        elif self.scene_description:
            parts.append(self.scene_description)

        # Objects
        objects = rec.get("objects", [])
        named_objs = [o["label"] for o in objects if o.get("confidence", 0) >= 0.5]
        if named_objs:
            parts.append(f"objects: {', '.join(named_objs[:4])}")

        # Gestures
        gestures = rec.get("gestures", [])
        active_gestures = [g["type"] for g in gestures if g.get("type") != "none"]
        if active_gestures:
            parts.append(f"gesture: {', '.join(active_gestures)}")

        body_lang = rec.get("body_language", "neutral")
        if body_lang not in ("neutral", "still"):
            parts.append(f"body: {body_lang}")

        # Rich persons data
        persons_data = []
        for p in self.persons:
            persons_data.append({
                "identity": rec.get("face_identity", "unknown"),
                "identity_id": rec.get("face_identity_id"),
                "face_confidence": rec.get("face_confidence", 0),
                "position": self._bbox_position(p.bbox, 320),
                "distance": self._bbox_distance(p.bbox, 320),
                "gaze": p.gaze,
                "expression": p.expression,
                "engagement": round(self.engagement, 2),
                "activity": self.activity,
                "confidence": round(p.confidence, 2),
                "micro_motion": round(p.micro_motion, 3),
                "landmarks": p.landmarks.to_dict(p.bbox[0], p.bbox[1]),
            })

        return {
            # Backward-compatible keys
            "summary": "; ".join(parts),
            "person_present": self.person_present,
            "faces": len(self.faces),
            "smiling": self.person_smiling,
            "brightness": self.brightness,
            "motion": round(self.motion_level, 1),
            # Rich keys
            "persons": persons_data,
            "activity": self.activity,
            "engagement": round(self.engagement, 2),
            "mood": self.mood,
            "attention_on_screen": self.attention_on_screen,
            "people_count": len(self.persons),
            "identities_known": len(self._identities),
            "environment_learned": self.env.calibrated,
            # Recognition data
            "face_identity": rec.get("face_identity"),
            "face_confidence": rec.get("face_confidence", 0),
            "face_match_type": rec.get("face_match_type", "none"),
            "known_faces": rec.get("known_faces", 0),
            "dominant_colors": rec.get("dominant_colors", []),
            "color_mood": rec.get("color_mood", "neutral"),
            "color_change": rec.get("color_change", False),
            "texture_type": rec.get("texture_type", "unknown"),
            "anomalies_detected": rec.get("anomalies_detected", 0),
            "anomaly_regions": rec.get("anomaly_regions", []),
            "objects": rec.get("objects", []),
            "object_count": rec.get("object_count", 0),
            "desk_layout": rec.get("desk_layout"),
            "scene_type": rec.get("scene_type", "unknown"),
            "scene_details": rec.get("scene_details", ""),
            "scene_confidence": rec.get("scene_confidence", 0),
            "gestures": rec.get("gestures", []),
            "body_language": rec.get("body_language", "neutral"),
            "hands_visible": rec.get("hands_visible", 0),
        }

    # ══════════════════════════════════════════════════════════════════
    # LAYER 1: Chromatic Skin Detection
    # ══════════════════════════════════════════════════════════════════

    def _detect_skin(self, ycrcb: np.ndarray) -> np.ndarray:
        """Detect skin pixels using YCrCb color space. Works across all skin tones."""
        cr = ycrcb[:, :, 1]
        cb = ycrcb[:, :, 2]

        cr_lo, cr_hi = self.env.skin_cr_range
        cb_lo, cb_hi = self.env.skin_cb_range

        mask = (cr >= cr_lo) & (cr <= cr_hi) & (cb >= cb_lo) & (cb <= cb_hi)

        # Morphological cleanup via numpy (no cv2.morphologyEx needed)
        # Simple erosion: a pixel survives only if all 4 neighbors are also skin
        eroded = mask[1:-1, 1:-1] & mask[:-2, 1:-1] & mask[2:, 1:-1] & mask[1:-1, :-2] & mask[1:-1, 2:]
        clean = np.zeros_like(mask)
        clean[1:-1, 1:-1] = eroded

        # Dilation: expand surviving pixels
        dilated = np.zeros_like(clean)
        dilated[1:-1, 1:-1] = clean[1:-1, 1:-1] | clean[:-2, 1:-1] | clean[2:, 1:-1] | clean[1:-1, :-2] | clean[1:-1, 2:]

        return dilated.astype(np.uint8)

    def _find_skin_blobs(self, mask: np.ndarray, h: int, w: int) -> list[dict]:
        """Find connected skin regions. Returns list of blob dicts with bbox + stats."""
        num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)

        blobs = []
        min_area = (h * w) * 0.005  # at least 0.5% of frame
        max_area = (h * w) * 0.5    # at most 50%

        for i in range(1, num_labels):  # skip background (label 0)
            area = stats[i, cv2.CC_STAT_AREA]
            if area < min_area or area > max_area:
                continue

            x = stats[i, cv2.CC_STAT_LEFT]
            y = stats[i, cv2.CC_STAT_TOP]
            bw = stats[i, cv2.CC_STAT_WIDTH]
            bh = stats[i, cv2.CC_STAT_HEIGHT]

            # Aspect ratio filter: faces are roughly square to tall rectangle
            aspect = bh / max(bw, 1)
            if aspect < 0.5 or aspect > 3.0:
                continue

            blobs.append({
                "bbox": (x, y, bw, bh),
                "area": area,
                "centroid": (centroids[i][0], centroids[i][1]),
                "label_id": i,
                "mask_slice": (labels[y:y+bh, x:x+bw] == i).astype(np.uint8),
            })

        # Sort by area descending (biggest blob first)
        blobs.sort(key=lambda b: b["area"], reverse=True)
        return blobs[:5]  # max 5 candidates

    # ══════════════════════════════════════════════════════════════════
    # LAYER 2: Bilateral Symmetry
    # ══════════════════════════════════════════════════════════════════

    def _symmetry_score(self, roi_gray: np.ndarray) -> float:
        """Compute bilateral symmetry of a region. Faces score > 0.6."""
        if roi_gray.size < 100:
            return 0.0

        h, w = roi_gray.shape
        # Take the center 80% to avoid edge artifacts
        margin_x = w // 10
        margin_y = h // 10
        roi = roi_gray[margin_y:h-margin_y, margin_x:w-margin_x].astype(np.float32)

        if roi.size < 50:
            return 0.0

        flipped = roi[:, ::-1]

        # Normalized cross-correlation
        mean_a = np.mean(roi)
        mean_b = np.mean(flipped)
        std_a = np.std(roi)
        std_b = np.std(flipped)

        if std_a < 1e-6 or std_b < 1e-6:
            return 0.0

        ncc = np.mean((roi - mean_a) * (flipped - mean_b)) / (std_a * std_b)
        return float(np.clip(ncc, 0, 1))

    # ══════════════════════════════════════════════════════════════════
    # LAYER 3: Edge Structure Analysis
    # ══════════════════════════════════════════════════════════════════

    def _edge_structure(self, roi_gray: np.ndarray) -> tuple[np.ndarray, str]:
        """Analyze edge distribution in a face candidate.

        Returns (structure_vector[3], expression_estimate).
        structure_vector: [horizontal_density, vertical_density, mouth_void_ratio]
        """
        if roi_gray.size < 100:
            return np.zeros(3), "neutral"

        h, w = roi_gray.shape

        # Sobel gradients
        sx = cv2.Sobel(roi_gray, cv2.CV_32F, 1, 0, ksize=3)
        sy = cv2.Sobel(roi_gray, cv2.CV_32F, 0, 1, ksize=3)

        horiz_edges = np.abs(sy)
        vert_edges = np.abs(sx)

        # Divide into thirds: upper (eyes/brows), middle (nose), lower (mouth)
        third = h // 3
        upper = slice(0, third)
        middle = slice(third, 2 * third)
        lower = slice(2 * third, h)

        # Horizontal edge density in upper third (eyes/brows produce strong horizontal edges)
        horiz_upper = float(np.mean(horiz_edges[upper, :]))

        # Vertical edge density in middle (nose ridge)
        center_strip = slice(w // 3, 2 * w // 3)
        vert_middle = float(np.mean(vert_edges[middle, center_strip]))

        # Mouth void ratio: less edge in lower center when mouth closed
        lower_center_edges = float(np.mean(horiz_edges[lower, center_strip]))
        upper_edges = float(np.mean(horiz_edges[upper, :]))

        # Normalize
        total = horiz_upper + vert_middle + lower_center_edges + 1e-6
        structure = np.array([
            horiz_upper / total,
            vert_middle / total,
            lower_center_edges / total,
        ])

        # Expression: high lower edge = mouth open or smiling
        expression = "neutral"
        if upper_edges > 0:
            mouth_ratio = lower_center_edges / (upper_edges + 1e-6)
            if mouth_ratio > 0.8:
                expression = "smiling"
            elif mouth_ratio > 0.6:
                expression = "open_mouth"

        return structure, expression

    # ══════════════════════════════════════════════════════════════════
    # LAYER 4: Temporal Motion Field
    # ══════════════════════════════════════════════════════════════════

    def _compute_motion_field(self, gray: np.ndarray, h: int, w: int) -> np.ndarray:
        """Compute 8x6 motion grid from frame difference.

        Returns 48-element vector (motion intensity per cell).
        """
        grid = np.zeros(48)

        if self._prev_gray is None:
            self._motion_grid_history.append(grid)
            return grid

        diff = cv2.absdiff(self._prev_gray, gray)

        # Reshape into 6 rows x 8 cols of cells
        cell_h = h // 6
        cell_w = w // 8

        for row in range(6):
            for col in range(8):
                cell = diff[row*cell_h:(row+1)*cell_h, col*cell_w:(col+1)*cell_w]
                grid[row * 8 + col] = float(np.mean(cell))

        self._motion_grid_history.append(grid)
        return grid

    def _detect_micro_motion(self) -> float:
        """Detect biological micro-motion (breathing, fidgeting) from motion history.

        Looks for small, periodic oscillation in motion grid cells.
        Returns intensity 0-1.
        """
        if len(self._motion_grid_history) < 5:
            return 0.0

        history = np.array(list(self._motion_grid_history))  # (N, 48)

        # Look for cells with low but persistent variation (micro-motion)
        cell_stds = np.std(history, axis=0)
        cell_means = np.mean(history, axis=0)

        # Micro-motion: cells where std is 1-10 (small oscillation, not noise or macro-motion)
        micro_mask = (cell_stds > 1.0) & (cell_stds < 10.0) & (cell_means < 15.0)
        micro_cells = np.sum(micro_mask)

        # Periodicity check via autocorrelation on top micro-motion cells
        periodicity = 0.0
        if micro_cells > 0 and len(history) >= 8:
            # Average the micro-motion cells over time
            micro_signal = np.mean(history[:, micro_mask], axis=1)
            if len(micro_signal) >= 8:
                # Simple autocorrelation at lag 2-4 (breathing at 2s intervals ≈ 0.25Hz)
                micro_signal = micro_signal - np.mean(micro_signal)
                norm = np.sum(micro_signal ** 2)
                if norm > 0:
                    for lag in range(2, min(5, len(micro_signal))):
                        corr = np.sum(micro_signal[:-lag] * micro_signal[lag:]) / norm
                        periodicity = max(periodicity, corr)

        # Combine: more micro-cells + higher periodicity = stronger signal
        intensity = min(1.0, (micro_cells / 12.0) * (0.5 + periodicity))
        return float(intensity)

    # ══════════════════════════════════════════════════════════════════
    # LAYER 5: Appearance Fingerprinting
    # ══════════════════════════════════════════════════════════════════

    def _compute_appearance(self, roi_hsv: np.ndarray, skin_tone: np.ndarray,
                            bbox: tuple) -> np.ndarray:
        """Compute 71-element appearance vector for identity matching."""
        # HSV histogram: 16 hue bins × 4 saturation bins = 64
        h_bins = np.clip(roi_hsv[:, :, 0].ravel() // 12, 0, 15).astype(int)  # 0-15
        s_bins = np.clip(roi_hsv[:, :, 1].ravel() // 64, 0, 3).astype(int)   # 0-3

        hist = np.zeros(64)
        for i in range(len(h_bins)):
            hist[h_bins[i] * 4 + s_bins[i]] += 1

        total = np.sum(hist)
        if total > 0:
            hist /= total

        # Skin tone centroid in YCrCb (3 values, normalized 0-1)
        skin_norm = skin_tone / 255.0

        # Geometry: bbox aspect ratio + relative position (4 values)
        x, y, w, bh = bbox
        frame_w, frame_h = 320, 240
        geom = np.array([
            bh / max(w, 1),              # aspect ratio
            (x + w/2) / frame_w,          # relative x center
            (y + bh/2) / frame_h,         # relative y center
            (w * bh) / (frame_w * frame_h) # relative area
        ])

        return np.concatenate([hist, skin_norm, geom])

    def _match_identity(self, person: PersonSnapshot, now: float):
        """Match a person to known identities or create new one."""
        if person.appearance is None or np.sum(np.abs(person.appearance)) < 0.01:
            return

        best_id = None
        best_sim = 0.0

        for pid, profile in self._identities.items():
            if profile.count < 2:
                continue
            sim = self._cosine_similarity(person.appearance, profile.centroid)
            if sim > best_sim:
                best_sim = sim
                best_id = pid

        if best_sim > 0.65 and best_id:
            # Update existing identity
            profile = self._identities[best_id]
            n = profile.count
            profile.centroid = (profile.centroid * n + person.appearance) / (n + 1)
            profile.variance = (profile.variance * n + (person.appearance - profile.centroid) ** 2) / (n + 1)
            if person.motion_sig is not None:
                profile.motion_style = (profile.motion_style * n + person.motion_sig) / (n + 1)
            profile.count += 1
            profile.last_seen = now
            profile.total_time += self.face_interval
            self._current_identity = best_id

            # Auto-label primary user
            if profile.count >= 50 and profile.label == "unknown":
                # Check if this identity is present most of the time
                total_observed = sum(1 for _, present in self._presence_history if present)
                if total_observed > 0 and profile.count / max(total_observed, 1) > 0.5:
                    profile.label = "primary_user"
                    print(f"[CORTEX] Primary user identified after {profile.count} observations")
        else:
            # New identity
            new_id = f"person_{self._next_identity_id}"
            self._next_identity_id += 1
            profile = IdentityProfile(new_id)
            profile.centroid = person.appearance.copy()
            profile.first_seen = now
            profile.last_seen = now
            profile.count = 1
            if person.motion_sig is not None:
                profile.motion_style = person.motion_sig.copy()
            self._identities[new_id] = profile
            self._current_identity = new_id

    @staticmethod
    def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
        dot = np.dot(a, b)
        norm = np.linalg.norm(a) * np.linalg.norm(b)
        if norm < 1e-8:
            return 0.0
        return float(dot / norm)

    # ══════════════════════════════════════════════════════════════════
    # LAYER 6: Spatial Attention (Gaze Estimation)
    # ══════════════════════════════════════════════════════════════════

    def _estimate_gaze(self, roi_gray: np.ndarray) -> str:
        """Estimate gaze direction from edge distribution in the eye region."""
        h, w = roi_gray.shape
        if h < 20 or w < 20:
            return "unknown"

        # Focus on upper third (eye region)
        eye_region = roi_gray[:h//3, :]
        eh, ew = eye_region.shape

        if eh < 5 or ew < 10:
            return "unknown"

        # Horizontal edge density (detects eyes)
        sy = cv2.Sobel(eye_region, cv2.CV_32F, 0, 1, ksize=3)
        edge_map = np.abs(sy)

        # Split into left/center/right thirds
        third = ew // 3
        left_energy = float(np.mean(edge_map[:, :third]))
        center_energy = float(np.mean(edge_map[:, third:2*third]))
        right_energy = float(np.mean(edge_map[:, 2*third:]))

        total = left_energy + center_energy + right_energy + 1e-6

        # Vertical gaze: check edge density in lower half of face (looking down)
        lower_half = roi_gray[h//2:, :]
        sx_lower = cv2.Sobel(lower_half, cv2.CV_32F, 1, 0, ksize=3)
        lower_energy = float(np.mean(np.abs(sx_lower)))

        # Determine gaze
        if center_energy / total > 0.38:
            # Check if looking down (less edge in eye region, more in lower)
            upper_total = float(np.mean(edge_map))
            if upper_total > 0 and lower_energy / (upper_total + 1e-6) > 1.5:
                self.attention_on_screen = False
                return "down"
            self.attention_on_screen = True
            return "at_camera"
        elif left_energy > right_energy * 1.4:
            self.attention_on_screen = False
            return "left"
        elif right_energy > left_energy * 1.4:
            self.attention_on_screen = False
            return "right"

        self.attention_on_screen = True
        return "at_camera"

    # ══════════════════════════════════════════════════════════════════
    # LAYER 7: Facial Landmark Detection
    # ══════════════════════════════════════════════════════════════════

    def _detect_landmarks(self, roi_gray: np.ndarray, skin_roi: np.ndarray) -> FaceLandmarks:
        """Detect eyes, nose, mouth, and ears from edge + intensity analysis.

        Algorithm (no pretrained models):
        - Eyes: darkest horizontal pair in upper third, validated by edge density
        - Nose: vertical edge concentration at center, tip = darkest point in mid-center
        - Mouth: horizontal edge cluster in lower third, widest dark gap
        - Ears: skin-edge boundary at the left/right extremes of the face
        """
        lm = FaceLandmarks()
        h, w = roi_gray.shape
        if h < 20 or w < 15:
            return lm

        # Precompute edges
        sy = cv2.Sobel(roi_gray, cv2.CV_32F, 0, 1, ksize=3)
        sx = cv2.Sobel(roi_gray, cv2.CV_32F, 1, 0, ksize=3)
        horiz_edges = np.abs(sy)
        vert_edges = np.abs(sx)

        found = 0

        # ── EYES: darkest horizontal-edge pair in upper 40% ──
        eye_band = roi_gray[h // 6 : int(h * 0.42), :]
        eye_edges = horiz_edges[h // 6 : int(h * 0.42), :]
        eh, ew = eye_band.shape

        if eh > 5 and ew > 10:
            # Weighted map: dark pixels with strong horizontal edges = eyes
            inv_bright = 255.0 - eye_band.astype(np.float32)
            eye_map = inv_bright * (eye_edges / (np.max(eye_edges) + 1e-6))

            # Split left/right halves
            mid_x = ew // 2
            left_half = eye_map[:, :mid_x]
            right_half = eye_map[:, mid_x:]

            if left_half.size > 0 and right_half.size > 0:
                # Find peak in each half
                ly, lx = np.unravel_index(np.argmax(left_half), left_half.shape)
                ry, rx = np.unravel_index(np.argmax(right_half), right_half.shape)

                lm.left_eye = (int(lx), int(ly + h // 6))
                lm.right_eye = (int(rx + mid_x), int(ry + h // 6))
                lm.eye_distance = float(np.sqrt((lm.right_eye[0] - lm.left_eye[0]) ** 2 +
                                                  (lm.right_eye[1] - lm.left_eye[1]) ** 2))
                # Face tilt from eye line angle
                dx = lm.right_eye[0] - lm.left_eye[0]
                dy = lm.right_eye[1] - lm.left_eye[1]
                lm.face_tilt = float(np.degrees(np.arctan2(dy, max(dx, 1))))
                found += 2

        # ── NOSE: vertical edge peak at center, lower than eyes ──
        nose_band = roi_gray[int(h * 0.35) : int(h * 0.65), w // 4 : 3 * w // 4]
        nose_vedge = vert_edges[int(h * 0.35) : int(h * 0.65), w // 4 : 3 * w // 4]
        nh, nw = nose_band.shape

        if nh > 3 and nw > 3:
            # Nose tip: where vertical edges converge + dark spot below
            # Column-wise edge sum to find center ridge
            col_energy = np.sum(nose_vedge, axis=0)
            nose_col = int(np.argmax(col_energy))

            # Row with maximum edge in that column region (+/- 2 cols)
            col_lo = max(0, nose_col - 2)
            col_hi = min(nw, nose_col + 3)
            strip = nose_vedge[:, col_lo:col_hi]
            nose_row = int(np.argmax(np.mean(strip, axis=1)))

            lm.nose = (int(nose_col + w // 4), int(nose_row + h * 0.35))
            found += 1

        # ── MOUTH: horizontal edge cluster in lower third ──
        mouth_band = roi_gray[int(h * 0.6) : int(h * 0.85), w // 5 : 4 * w // 5]
        mouth_hedge = horiz_edges[int(h * 0.6) : int(h * 0.85), w // 5 : 4 * w // 5]
        mh, mw = mouth_band.shape

        if mh > 3 and mw > 5:
            # Mouth = strongest horizontal edge band (lips create strong horizontal lines)
            row_energy = np.mean(mouth_hedge, axis=1)
            mouth_row = int(np.argmax(row_energy))

            # Center of the mouth region
            mouth_line = mouth_hedge[max(0, mouth_row - 1):mouth_row + 2, :]
            if mouth_line.size > 0:
                col_energy = np.mean(mouth_line, axis=0)
                mouth_col = int(np.argmax(col_energy))
                # Use centroid of high-energy region for width
                threshold = np.max(col_energy) * 0.4
                active = col_energy > threshold
                if np.any(active):
                    cols = np.where(active)[0]
                    mouth_center = int(np.mean(cols))
                else:
                    mouth_center = mouth_col

                lm.mouth = (int(mouth_center + w // 5), int(mouth_row + h * 0.6))
                found += 1

        # ── EARS: skin boundary at face extremes ──
        # Ears are at the leftmost/rightmost extent of skin, roughly at eye level
        ear_y_start = h // 5
        ear_y_end = int(h * 0.55)
        ear_band_skin = skin_roi[ear_y_start:ear_y_end, :]

        if ear_band_skin.shape[0] > 3 and ear_band_skin.shape[1] > 10:
            # Left ear: leftmost skin pixel column, averaged across rows
            row_sums = np.sum(ear_band_skin, axis=0)
            skin_cols = np.where(row_sums > 0)[0]

            if len(skin_cols) > 4:
                # Left ear at the left skin boundary
                left_edge = skin_cols[0]
                # Find the row with the leftmost skin at that column range
                left_region = ear_band_skin[:, left_edge:min(left_edge + 5, w)]
                if left_region.size > 0:
                    left_rows = np.where(np.any(left_region > 0, axis=1))[0]
                    if len(left_rows) > 0:
                        ear_row = int(np.mean(left_rows))
                        lm.left_ear = (int(left_edge), int(ear_row + ear_y_start))
                        found += 1

                # Right ear at the right skin boundary
                right_edge = skin_cols[-1]
                right_region = ear_band_skin[:, max(0, right_edge - 4):right_edge + 1]
                if right_region.size > 0:
                    right_rows = np.where(np.any(right_region > 0, axis=1))[0]
                    if len(right_rows) > 0:
                        ear_row = int(np.mean(right_rows))
                        lm.right_ear = (int(right_edge), int(ear_row + ear_y_start))
                        found += 1

        lm.confidence = min(1.0, found / 6.0)
        return lm

    # ══════════════════════════════════════════════════════════════════
    # BLOB → PERSON ANALYSIS (fuses layers 1-6)
    # ══════════════════════════════════════════════════════════════════

    def _analyze_blob(self, blob: dict, frame: np.ndarray, gray: np.ndarray,
                      hsv: np.ndarray, skin_mask: np.ndarray,
                      motion_grid: np.ndarray, now: float) -> PersonSnapshot | None:
        """Analyze a skin blob through all cortical layers. Returns PersonSnapshot or None."""
        x, y, bw, bh = blob["bbox"]
        h, w = frame.shape[:2]

        # Extract ROIs
        roi_gray = gray[y:y+bh, x:x+bw]
        roi_hsv = hsv[y:y+bh, x:x+bw]

        if roi_gray.size < 100:
            return None

        # Layer 2: Symmetry
        sym = self._symmetry_score(roi_gray)

        # Layer 3: Edge structure
        structure, expression = self._edge_structure(roi_gray)

        # Face-like edge structure score (eyes stronger than mouth, nose present)
        edge_score = 0.0
        if structure[0] > 0.25:  # horizontal edges in upper third (eyes)
            edge_score += 0.5
        if structure[1] > 0.1:   # vertical edges in middle (nose)
            edge_score += 0.3
        if structure[2] < structure[0]:  # mouth region less than eye region
            edge_score += 0.2

        # Layer 4: Micro-motion
        micro = self._detect_micro_motion()

        # Layer 6: Gaze
        gaze = self._estimate_gaze(roi_gray)

        # Layer 7: Facial landmarks
        roi_skin = skin_mask[y:y+bh, x:x+bw]
        landmarks = self._detect_landmarks(roi_gray, roi_skin)

        # ── Confidence fusion ──
        skin_score = min(1.0, blob["area"] / (h * w * 0.02))  # normalized skin area
        confidence = (
            0.30 * skin_score +
            0.25 * sym +
            0.25 * edge_score +
            0.20 * min(1.0, micro * 3)  # boost micro-motion contribution
        )

        if confidence < 0.3:
            return None

        # Layer 5: Appearance fingerprint
        # Get mean skin tone from YCrCb in this region
        roi_skin = skin_mask[y:y+bh, x:x+bw]
        skin_pixels_cr = frame[y:y+bh, x:x+bw][roi_skin > 0]
        if len(skin_pixels_cr) > 10:
            skin_tone = np.mean(skin_pixels_cr, axis=0)
        else:
            skin_tone = np.array([128, 150, 100], dtype=np.float32)

        appearance = self._compute_appearance(roi_hsv, skin_tone, blob["bbox"])

        # Build snapshot
        snap = PersonSnapshot()
        snap.timestamp = now
        snap.bbox = blob["bbox"]
        snap.skin_area = skin_score
        snap.symmetry = sym
        snap.face_structure = structure
        snap.appearance = appearance
        snap.motion_sig = motion_grid
        snap.gaze = gaze
        snap.expression = expression
        snap.micro_motion = micro
        snap.confidence = confidence
        snap.landmarks = landmarks

        return snap

    # ══════════════════════════════════════════════════════════════════
    # ACTIVITY & ENGAGEMENT INFERENCE
    # ══════════════════════════════════════════════════════════════════

    def _infer_activity(self):
        """Infer user activity from temporal motion patterns."""
        if len(self._motion_grid_history) < 3:
            self.activity = "present"
            return

        history = np.array(list(self._motion_grid_history))
        recent = history[-3:]  # last 3 frames (~6 seconds)

        # Mean motion in upper body region (rows 0-2, roughly)
        upper_motion = float(np.mean(recent[:, :24]))  # first 3 rows × 8 cols
        lower_motion = float(np.mean(recent[:, 24:]))   # last 3 rows × 8 cols
        total_motion = float(np.mean(recent))

        # Activity classification
        if total_motion < 1.0:
            self.activity = "idle"
        elif upper_motion > 5.0 and lower_motion < 3.0:
            self.activity = "working"
        elif total_motion > 15.0:
            self.activity = "active"
        elif upper_motion > 3.0:
            self.activity = "engaged"
        else:
            self.activity = "present"

        # Engagement scoring
        gaze_score = 1.0 if self.attention_on_screen else 0.3
        motion_score = min(1.0, upper_motion / 10.0)
        presence_score = min(1.0, len(self._snapshot_history) / 20.0)
        expression_score = 0.7 if self.person_smiling else 0.4

        self.engagement = (
            0.40 * gaze_score +
            0.25 * motion_score +
            0.20 * presence_score +
            0.15 * expression_score
        )

        # Mood estimation
        if self.engagement > 0.7 and self.person_smiling:
            self.mood = "happy"
        elif self.engagement > 0.6:
            self.mood = "focused"
        elif self.engagement > 0.4:
            self.mood = "relaxed"
        elif total_motion > 10:
            self.mood = "restless"
        else:
            self.mood = "calm"

    # ══════════════════════════════════════════════════════════════════
    # SCENE & ENVIRONMENT
    # ══════════════════════════════════════════════════════════════════

    def _analyze_scene(self, gray: np.ndarray):
        """Quick scene complexity analysis."""
        edges = cv2.Canny(gray, 50, 150)
        h, w = gray.shape[:2]
        edge_ratio = float(np.sum(edges > 0)) / (h * w)

        if edge_ratio > 0.15:
            self.scene_description = "complex scene"
        elif edge_ratio > 0.05:
            self.scene_description = "moderate detail"
        else:
            self.scene_description = "simple background"

    def _update_environment(self, frame: np.ndarray, gray: np.ndarray):
        """Learn the environment over time."""
        # Update lighting baseline
        mean_b = float(np.mean(gray))
        self.env.lighting_baseline = self.env.lighting_baseline * 0.95 + mean_b * 0.05

        # Update background model when no person detected
        if not self.person_present:
            self.env.empty_frames += 1
            frame_f = frame.astype(np.float32)

            if self.env.background is None:
                self.env.background = frame_f
                self.env.bg_variance = np.zeros(gray.shape, dtype=np.float32)
            else:
                self.env.background = self.env.background * 0.95 + frame_f * 0.05
                gray_f = gray.astype(np.float32)
                bg_gray = np.mean(self.env.background, axis=2)
                diff = np.abs(gray_f - bg_gray)
                self.env.bg_variance = self.env.bg_variance * 0.95 + diff * 0.05

            if self.env.empty_frames > 20 and not self.env.calibrated:
                self.env.calibrated = True
                print(f"[CORTEX] Environment learned: baseline lighting={self.env.lighting_baseline:.0f}")
        else:
            self.env.empty_frames = 0

        # Adapt skin detection thresholds based on lighting
        if self.env.calibrated:
            ratio = mean_b / max(self.env.lighting_baseline, 1)
            if ratio < 0.7:
                # Darker: widen skin range
                self.env.skin_cr_range = (128, 178)
                self.env.skin_cb_range = (72, 132)
            elif ratio > 1.3:
                # Brighter: tighten
                self.env.skin_cr_range = (138, 168)
                self.env.skin_cb_range = (82, 122)
            else:
                self.env.skin_cr_range = (133, 173)
                self.env.skin_cb_range = (77, 127)

    # ══════════════════════════════════════════════════════════════════
    # UTILITIES
    # ══════════════════════════════════════════════════════════════════

    def _decode(self, frame_bytes: bytes) -> np.ndarray | None:
        """Decode JPEG bytes to OpenCV BGR frame."""
        try:
            arr = np.frombuffer(frame_bytes, dtype=np.uint8)
            return cv2.imdecode(arr, cv2.IMREAD_COLOR)
        except Exception:
            return None

    @staticmethod
    def _bbox_position(bbox: tuple, frame_w: int) -> str:
        x, _, w, _ = bbox
        center_x = x + w // 2
        if abs(center_x - frame_w // 2) < frame_w // 4:
            return "center"
        return "left" if center_x < frame_w // 2 else "right"

    @staticmethod
    def _bbox_distance(bbox: tuple, frame_w: int) -> str:
        _, _, w, _ = bbox
        if w > frame_w * 0.3:
            return "close"
        if w > frame_w * 0.15:
            return "medium"
        return "far"


# Backward-compatible alias
AmbientViewer = CorticalViewer
