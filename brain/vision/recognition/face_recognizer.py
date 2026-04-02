"""Face Recognition — geometry-based identity learning. No dlib, no pretrained models.

Builds a 97-dimensional face signature from:
- Existing 71-dim appearance vector (HSV histogram + skin tone + geometry)
- 12-dim face geometry (edge ratios, symmetry, proportions)
- 6-dim skin tone signature (Cr/Cb mean + std)
- 8-dim contour shape (Hu moments + solidity)

Learns identities over time and persists to disk.
"""

import numpy as np
import cv2
import time
from collections import deque
from brain.vision.recognition.persistence import save_json, load_json, encode_array, decode_array


class FaceIdentity:
    """A learned face identity."""

    def __init__(self, identity_id: str):
        self.id = identity_id
        self.label = "unknown"
        self.centroid: np.ndarray = np.zeros(97)
        self.variance: np.ndarray = np.ones(97)
        self.count: int = 0
        self.first_seen: float = 0
        self.last_seen: float = 0
        self.total_time: float = 0
        self.recent_vectors: deque = deque(maxlen=10)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "centroid": encode_array(self.centroid),
            "variance": encode_array(self.variance),
            "count": self.count,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "total_time": self.total_time,
        }

    @staticmethod
    def from_dict(d: dict) -> 'FaceIdentity':
        f = FaceIdentity(d["id"])
        f.label = d.get("label", "unknown")
        f.centroid = decode_array(d["centroid"], 97)
        f.variance = decode_array(d["variance"], 97)
        f.count = d.get("count", 0)
        f.first_seen = d.get("first_seen", 0)
        f.last_seen = d.get("last_seen", 0)
        f.total_time = d.get("total_time", 0)
        return f


class FaceRecognizer:
    """Learns and identifies faces from geometric + appearance signatures."""

    def __init__(self):
        self.bank: dict[str, FaceIdentity] = {}
        self._next_id: int = 0
        self._save_counter: int = 0
        self._load()

        # Current frame results
        self.current_identity: str | None = None
        self.current_confidence: float = 0.0
        self.match_type: str = "none"

    def process(self, frame: np.ndarray, gray: np.ndarray,
                skin_mask: np.ndarray, persons: list, ycrcb: np.ndarray,
                **kwargs) -> dict:
        """Process detected persons and match/learn identities."""
        if not persons:
            self.current_identity = None
            self.current_confidence = 0.0
            self.match_type = "none"
            return self._result()

        now = time.time()

        for person in persons:
            x, y, bw, bh = person.bbox
            if bw < 15 or bh < 15:
                continue

            # Build the full 97-dim signature
            appearance_71 = person.appearance  # already computed by CorticalViewer
            geometry_12 = self._compute_geometry(gray[y:y+bh, x:x+bw])
            skin_sig_6 = self._compute_skin_signature(ycrcb[y:y+bh, x:x+bw], skin_mask[y:y+bh, x:x+bw])
            contour_8 = self._compute_contour_shape(skin_mask[y:y+bh, x:x+bw])

            signature = np.concatenate([appearance_71, geometry_12, skin_sig_6, contour_8])

            # Check if we're enrolling a Face ID
            landmarks = getattr(person, 'landmarks', None)
            enrollment_result = self._check_enrollment(signature, landmarks)
            if enrollment_result:
                self._last_enrollment_result = enrollment_result

            # Match against known identities
            best_id, best_score = self._match(signature)

            if best_score >= 0.75:
                self.match_type = "confident"
                self.current_identity = best_id
                self.current_confidence = best_score
                self._update_identity(best_id, signature, now)
            elif best_score >= 0.55:
                self.match_type = "likely"
                self.current_identity = best_id
                self.current_confidence = best_score
                self._update_identity(best_id, signature, now)
            else:
                # New face
                self.match_type = "new"
                new_id = self._create_identity(signature, now)
                self.current_identity = new_id
                self.current_confidence = 1.0

        # Auto-save periodically
        self._save_counter += 1
        if self._save_counter % 10 == 0:
            self._save()

        return self._result()

    def _compute_geometry(self, face_gray: np.ndarray) -> np.ndarray:
        """Compute 12-dim face geometry vector from edge analysis."""
        h, w = face_gray.shape
        if h < 10 or w < 10:
            return np.zeros(12)

        sx = cv2.Sobel(face_gray, cv2.CV_32F, 1, 0, ksize=3)
        sy = cv2.Sobel(face_gray, cv2.CV_32F, 0, 1, ksize=3)
        horiz = np.abs(sy)
        vert = np.abs(sx)

        third_h = h // 3
        third_w = w // 3

        # Divide into 3×3 zones
        zones_h = [horiz[i*third_h:(i+1)*third_h, :] for i in range(3)]
        zones_v = [vert[i*third_h:(i+1)*third_h, :] for i in range(3)]

        total = float(np.mean(horiz) + np.mean(vert) + 1e-6)

        geo = np.array([
            float(np.mean(zones_h[0])) / total,          # eye region horizontal edges
            float(np.mean(zones_h[1])) / total,          # nose region horizontal edges
            float(np.mean(zones_h[2])) / total,          # mouth region horizontal edges
            float(np.mean(zones_v[0])) / total,          # eye region vertical edges
            float(np.mean(zones_v[1])) / total,          # nose region vertical edges
            float(np.mean(zones_v[2])) / total,          # mouth region vertical edges
            # Lateral symmetry per zone
            self._zone_symmetry(face_gray[:third_h, :]),
            self._zone_symmetry(face_gray[third_h:2*third_h, :]),
            self._zone_symmetry(face_gray[2*third_h:, :]),
            # Proportions
            h / max(w, 1),                                # aspect ratio
            float(np.std(face_gray[:third_h, :])) / (float(np.std(face_gray)) + 1e-6),
            float(np.mean(face_gray[third_h:2*third_h, third_w:2*third_w])) / (float(np.mean(face_gray)) + 1e-6),
        ])
        return geo

    @staticmethod
    def _zone_symmetry(zone: np.ndarray) -> float:
        if zone.size < 10:
            return 0.0
        h, w = zone.shape
        left = zone[:, :w//2].astype(np.float32)
        right = zone[:, w//2:w//2 + left.shape[1]][:, ::-1].astype(np.float32)
        if left.shape != right.shape:
            min_w = min(left.shape[1], right.shape[1])
            left = left[:, :min_w]
            right = right[:, :min_w]
        if left.size == 0:
            return 0.0
        std_l, std_r = np.std(left), np.std(right)
        if std_l < 1e-6 or std_r < 1e-6:
            return 0.0
        ncc = np.mean((left - np.mean(left)) * (right - np.mean(right))) / (std_l * std_r)
        return float(np.clip(ncc, 0, 1))

    @staticmethod
    def _compute_skin_signature(ycrcb_roi: np.ndarray, skin_roi: np.ndarray) -> np.ndarray:
        """6-dim skin tone signature: mean + std of Y, Cr, Cb within skin pixels."""
        skin_pixels = ycrcb_roi[skin_roi > 0]
        if len(skin_pixels) < 10:
            return np.array([128, 150, 100, 10, 10, 10], dtype=np.float32) / 255.0

        mean = np.mean(skin_pixels, axis=0)
        std = np.std(skin_pixels, axis=0)
        return np.concatenate([mean, std]).astype(np.float32) / 255.0

    @staticmethod
    def _compute_contour_shape(skin_roi: np.ndarray) -> np.ndarray:
        """8-dim contour descriptor: 7 Hu moments + solidity."""
        contours, _ = cv2.findContours(skin_roi, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return np.zeros(8)

        largest = max(contours, key=cv2.contourArea)
        area = cv2.contourArea(largest)
        if area < 10:
            return np.zeros(8)

        moments = cv2.moments(largest)
        hu = cv2.HuMoments(moments).flatten()
        # Log-transform Hu moments for better scale
        hu = -np.sign(hu) * np.log10(np.abs(hu) + 1e-10)

        hull = cv2.convexHull(largest)
        hull_area = cv2.contourArea(hull)
        solidity = area / (hull_area + 1e-6)

        return np.concatenate([hu, [solidity]])

    def _match(self, signature: np.ndarray) -> tuple[str | None, float]:
        """Match signature against all known identities. Returns (id, score)."""
        best_id = None
        best_score = 0.0

        for fid, identity in self.bank.items():

            # Weighted: appearance (0.4) + geometry (0.3) + skin (0.15) + contour (0.15)
            sim_app = self._cosine_sim(signature[:71], identity.centroid[:71])
            sim_geo = self._cosine_sim(signature[71:83], identity.centroid[71:83])
            sim_skin = 1.0 - min(1.0, float(np.linalg.norm(signature[83:89] - identity.centroid[83:89])))
            sim_contour = self._cosine_sim(signature[89:97], identity.centroid[89:97])

            score = 0.40 * sim_app + 0.30 * sim_geo + 0.15 * sim_skin + 0.15 * sim_contour

            if score > best_score:
                best_score = score
                best_id = fid

        return best_id, best_score

    @staticmethod
    def _cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
        norm = np.linalg.norm(a) * np.linalg.norm(b)
        if norm < 1e-8:
            return 0.0
        return float(np.clip(np.dot(a, b) / norm, 0, 1))

    def _update_identity(self, fid: str, signature: np.ndarray, now: float):
        identity = self.bank[fid]
        n = identity.count
        alpha = min(0.02, 1.0 / (n + 1))
        identity.centroid = identity.centroid * (1 - alpha) + signature * alpha
        identity.variance = identity.variance * (1 - alpha) + (signature - identity.centroid) ** 2 * alpha
        identity.count += 1
        identity.last_seen = now
        identity.total_time += 2.0  # ~2s per frame
        identity.recent_vectors.append(signature)

        # Auto-label primary user after 100 observations
        if identity.count >= 100 and identity.label == "unknown":
            # If this is the most-seen identity, label it
            max_count = max(i.count for i in self.bank.values())
            if identity.count >= max_count:
                identity.label = "primary_user"
                print(f"[CORTEX-FACE] Primary user learned: {fid} ({identity.count} observations)")

    def _create_identity(self, signature: np.ndarray, now: float) -> str:
        fid = f"face_{self._next_id}"
        self._next_id += 1
        identity = FaceIdentity(fid)
        identity.centroid = signature.copy()
        identity.first_seen = now
        identity.last_seen = now
        identity.count = 1
        identity.recent_vectors.append(signature)
        self.bank[fid] = identity
        print(f"[CORTEX-FACE] New face registered: {fid}")
        return fid

    def label_identity(self, fid: str, name: str):
        """Manually label an identity (e.g., 'Ulrich')."""
        if fid in self.bank:
            self.bank[fid].label = name
            self._save()

    def get_label(self, fid: str | None) -> str:
        if fid and fid in self.bank:
            return self.bank[fid].label
        return "unknown"

    def _save(self):
        data = {
            "next_id": self._next_id,
            "faces": {fid: identity.to_dict() for fid, identity in self.bank.items()},
        }
        save_json("faces.json", data)

    def _load(self):
        data = load_json("faces.json")
        if not data:
            return
        self._next_id = data.get("next_id", 0)
        for fid, d in data.get("faces", {}).items():
            try:
                self.bank[fid] = FaceIdentity.from_dict(d)
            except Exception:
                continue
        if self.bank:
            print(f"[CORTEX-FACE] Loaded {len(self.bank)} known faces")

    # ══════════════════════════════════════════════════════════════════
    # FACE ID — Apple-style enrollment and verification
    # ══════════════════════════════════════════════════════════════════

    def enroll_face_id(self, name: str) -> dict:
        """Start Face ID enrollment for a name.

        Call this, then the next detected face will be enrolled under this name.
        Requires multiple observations for a secure enrollment (like Apple's Face ID).
        """
        self._enrolling_name = name
        self._enrollment_vectors: list[np.ndarray] = []
        self._enrollment_target = 10  # need 10 observations from different angles
        return {
            "status": "enrolling",
            "name": name,
            "message": f"Look at the camera. I need {self._enrollment_target} good captures. "
                       f"Slowly turn your head left, right, up, down.",
        }

    def _check_enrollment(self, signature: np.ndarray, landmarks) -> dict | None:
        """Check if we're in enrollment mode and accumulate vectors."""
        if not hasattr(self, '_enrolling_name') or not self._enrolling_name:
            return None

        # Only accept if landmarks have reasonable confidence
        if landmarks and landmarks.confidence >= 0.3:
            # Add landmark geometry to signature for richer enrollment
            lm_vec = self._landmarks_to_vector(landmarks)
            enriched = np.concatenate([signature, lm_vec])
            self._enrollment_vectors.append(enriched)

        count = len(self._enrollment_vectors)

        if count < self._enrollment_target:
            return {
                "status": "capturing",
                "progress": count,
                "target": self._enrollment_target,
                "message": f"Captured {count}/{self._enrollment_target}. Keep moving your head slowly.",
            }

        # Enrollment complete — create/update identity with averaged vectors
        name = self._enrolling_name
        self._enrolling_name = None

        # Find existing or create new
        target_fid = None
        for fid, identity in self.bank.items():
            if identity.label == name:
                target_fid = fid
                break

        if target_fid is None:
            target_fid = f"face_{self._next_id}"
            self._next_id += 1
            identity = FaceIdentity(target_fid)
            identity.first_seen = time.time()
            self.bank[target_fid] = identity

        identity = self.bank[target_fid]
        identity.label = name

        # Compute centroid from enrollment vectors (use only the 97-dim part)
        vectors_97 = np.array([v[:97] for v in self._enrollment_vectors])
        identity.centroid = np.mean(vectors_97, axis=0)
        identity.variance = np.var(vectors_97, axis=0)
        identity.count = count
        identity.last_seen = time.time()

        # Store landmark vectors for Face ID verification
        lm_vectors = np.array([v[97:] for v in self._enrollment_vectors])
        identity._landmark_centroid = np.mean(lm_vectors, axis=0)
        identity._landmark_variance = np.var(lm_vectors, axis=0)

        self._save()
        self._enrollment_vectors = []

        print(f"[FACE-ID] Enrolled '{name}' with {count} captures")
        return {
            "status": "enrolled",
            "name": name,
            "id": target_fid,
            "captures": count,
            "message": f"Face ID enrolled for {name}. I'll recognize you now.",
        }

    def verify_face_id(self, name: str) -> dict:
        """Verify if the current face matches a specific enrolled identity (like Apple Face ID)."""
        target = None
        target_fid = None
        for fid, identity in self.bank.items():
            if identity.label.lower() == name.lower():
                target = identity
                target_fid = fid
                break

        if target is None:
            return {"verified": False, "reason": "not_enrolled",
                    "message": f"No Face ID enrolled for '{name}'."}

        if self.current_identity is None:
            return {"verified": False, "reason": "no_face",
                    "message": "No face currently detected."}

        # Direct identity match
        if self.current_identity == target_fid and self.current_confidence >= 0.65:
            return {"verified": True, "confidence": round(self.current_confidence, 2),
                    "message": f"Identity verified: {name}"}

        # Cross-check: compare current face's centroid against target
        current = self.bank.get(self.current_identity)
        if current is not None:
            sim = self._cosine_sim(current.centroid, target.centroid)
            if sim >= 0.7:
                return {"verified": True, "confidence": round(sim, 2),
                        "message": f"Identity verified: {name} (cross-match {sim:.0%})"}

        return {"verified": False, "reason": "mismatch",
                "confidence": round(self.current_confidence, 2),
                "message": "Face does not match."}

    def list_enrolled(self) -> list[dict]:
        """List all enrolled Face IDs."""
        return [
            {
                "id": fid,
                "name": identity.label,
                "observations": identity.count,
                "first_seen": identity.first_seen,
                "last_seen": identity.last_seen,
            }
            for fid, identity in self.bank.items()
            if identity.label != "unknown"
        ]

    @staticmethod
    def _landmarks_to_vector(landmarks) -> np.ndarray:
        """Convert facial landmarks to a geometric feature vector (14-dim).

        Captures the spatial relationships between facial features —
        these ratios are unique per person and scale-invariant.
        """
        vec = np.zeros(14)

        points = {
            'le': landmarks.left_eye,
            're': landmarks.right_eye,
            'n': landmarks.nose,
            'm': landmarks.mouth,
            'la': landmarks.left_ear,
            'ra': landmarks.right_ear,
        }

        # Use eye distance as normalization factor
        norm = landmarks.eye_distance if landmarks.eye_distance > 5 else 1.0

        def _dist(a, b):
            if a is None or b is None:
                return 0.0
            return float(np.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2))

        # 14 geometric ratios (scale-invariant)
        vec[0] = _dist(points['le'], points['re']) / norm        # eye-to-eye (should be ~1.0)
        vec[1] = _dist(points['le'], points['n']) / norm          # left eye to nose
        vec[2] = _dist(points['re'], points['n']) / norm          # right eye to nose
        vec[3] = _dist(points['n'], points['m']) / norm           # nose to mouth
        vec[4] = _dist(points['le'], points['m']) / norm          # left eye to mouth
        vec[5] = _dist(points['re'], points['m']) / norm          # right eye to mouth
        vec[6] = _dist(points['la'], points['ra']) / norm         # ear to ear (face width)
        vec[7] = _dist(points['la'], points['le']) / norm         # left ear to left eye
        vec[8] = _dist(points['ra'], points['re']) / norm         # right ear to right eye
        vec[9] = _dist(points['la'], points['n']) / norm          # left ear to nose
        vec[10] = _dist(points['ra'], points['n']) / norm         # right ear to nose
        vec[11] = landmarks.face_tilt / 45.0                       # normalized tilt
        # Triangle ratios (unique to each face)
        eye_nose = (_dist(points['le'], points['n']) + _dist(points['re'], points['n'])) / 2
        vec[12] = eye_nose / (norm + 1e-6)                        # avg eye-nose ratio
        nose_mouth = _dist(points['n'], points['m'])
        vec[13] = nose_mouth / (eye_nose + 1e-6)                  # nose-mouth / eye-nose ratio

        return vec

    # ══════════════════════════════════════════════════════════════════

    def _result(self) -> dict:
        # Check enrollment progress
        enrollment_status = None
        if hasattr(self, '_enrolling_name') and self._enrolling_name:
            enrollment_status = {
                "enrolling": self._enrolling_name,
                "progress": len(getattr(self, '_enrollment_vectors', [])),
                "target": getattr(self, '_enrollment_target', 10),
            }

        return {
            "face_identity": self.get_label(self.current_identity),
            "face_identity_id": self.current_identity,
            "face_confidence": round(self.current_confidence, 2),
            "face_match_type": self.match_type,
            "known_faces": len(self.bank),
            "face_id_enrollment": enrollment_status,
        }
