"""Recognition Engine — coordinates all recognition modules.

Called by CorticalViewer.feed() after person detection.
Runs: Color → Pattern → Face → Object → Scene → Gesture
Plus AI Vision: sends frames to Claude/GPT for deep understanding.
"""

import numpy as np
import cv2
import base64
import time
from src.vision.recognition.color_recognizer import ColorRecognizer
from src.vision.recognition.pattern_recognizer import PatternRecognizer
from src.vision.recognition.face_recognizer import FaceRecognizer
from src.vision.recognition.object_recognizer import ObjectRecognizer
from src.vision.recognition.scene_recognizer import SceneRecognizer
from src.vision.recognition.gesture_recognizer import GestureRecognizer


class RecognitionEngine:
    """Coordinates all recognition modules in the correct order."""

    def __init__(self):
        self.color = ColorRecognizer()
        self.pattern = PatternRecognizer()
        self.face = FaceRecognizer()
        self.objects = ObjectRecognizer()
        self.scene = SceneRecognizer()
        self.gesture = GestureRecognizer()

        # Merged results from last process() call
        self.results: dict = {}

        # AI Vision state
        self._last_ai_vision: str = ""
        self._last_ai_vision_time: float = 0
        self._ai_vision_interval: float = 10.0  # seconds between AI vision calls
        self._ai_vision_enabled: bool = False
        self._pending_vision_query: str | None = None
        self._last_frame_b64: str = ""

    def process(self, frame: np.ndarray, gray: np.ndarray,
                hsv: np.ndarray, ycrcb: np.ndarray,
                skin_mask: np.ndarray, motion_grid: np.ndarray,
                persons: list, env=None, **kwargs) -> dict:
        """Run all recognition modules. Returns merged result dict."""
        results = {}

        # Capture frame for AI vision queries
        self.capture_frame_b64(frame)

        # Phase 1: Color + Pattern (independent, no deps)
        color_result = self.color.process(frame=frame, hsv=hsv, persons=persons)
        results.update(color_result)

        pattern_result = self.pattern.process(gray=gray)
        results.update(pattern_result)

        # Phase 2: Face recognition (needs persons + skin + ycrcb)
        face_result = self.face.process(
            frame=frame, gray=gray, skin_mask=skin_mask,
            persons=persons, ycrcb=ycrcb,
        )
        results.update(face_result)

        # Phase 3: Object detection (needs env background, exclude persons)
        object_result = self.objects.process(
            frame=frame, gray=gray, hsv=hsv,
            env=env, persons=persons,
        )
        results.update(object_result)

        # Phase 4: Scene (uses color + object results)
        scene_result = self.scene.process(
            frame=frame, gray=gray, hsv=hsv,
            color_result=color_result, object_result=object_result,
            persons=persons,
        )
        results.update(scene_result)

        # Phase 5: Gesture (needs skin mask - face regions)
        gesture_result = self.gesture.process(
            frame=frame, gray=gray, skin_mask=skin_mask,
            persons=persons, motion_grid=motion_grid,
        )
        results.update(gesture_result)

        self.results = results
        return results

    def capture_frame_b64(self, frame: np.ndarray) -> str:
        """Encode current frame as base64 JPEG for AI vision."""
        _, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        self._last_frame_b64 = base64.b64encode(buf.tobytes()).decode('ascii')
        return self._last_frame_b64

    async def ask_vision(self, providers, prompt: str = "What do you see?",
                         frame: np.ndarray = None) -> str:
        """Send the current frame to an AI vision model for deep understanding.

        Sends the raw frame to a multimodal AI model that can understand
        everything in the image.
        """
        if frame is not None:
            self.capture_frame_b64(frame)

        if not self._last_frame_b64:
            return "No frame available."

        system = (
            "You are JARVIS's vision system. You're looking through a webcam. "
            "Describe what you see clearly and concisely — people, objects, text, "
            "actions, environment. Be specific about positions, colors, and details. "
            "If you see a person, describe what they're doing, wearing, and their expression. "
            "Identify any objects, text, screens, or notable items visible."
        )

        result, provider = await providers.query_vision(
            self._last_frame_b64, prompt, system
        )

        if result:
            self._last_ai_vision = result
            self._last_ai_vision_time = time.time()
            print(f"[CORTEX-AI] Vision via {provider}: {result[:100]}...")

        return result or "Could not analyze the image — no vision-capable provider available."

    async def auto_vision(self, providers, frame: np.ndarray) -> str | None:
        """Periodic AI vision — runs every N seconds if enabled.

        Returns description if new analysis was done, None otherwise.
        """
        if not self._ai_vision_enabled:
            return None

        now = time.time()
        if now - self._last_ai_vision_time < self._ai_vision_interval:
            return None

        self.capture_frame_b64(frame)
        prompt = self._pending_vision_query or "Briefly describe what you see. Focus on changes since last look."
        self._pending_vision_query = None

        result = await self.ask_vision(providers, prompt)
        return result if result else None

    def enable_ai_vision(self, enabled: bool = True, interval: float = 10.0):
        """Toggle continuous AI vision analysis."""
        self._ai_vision_enabled = enabled
        self._ai_vision_interval = max(5.0, interval)
        state = "ON" if enabled else "OFF"
        print(f"[CORTEX-AI] AI Vision {state} (every {self._ai_vision_interval}s)")

    def save_all(self):
        """Persist all learned data."""
        self.face._save()
        self.objects._save()
