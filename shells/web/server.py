"""JARVIS Web Shell — HTTP + WebSocket + Neural TTS server."""

import asyncio
import json
import time
import io
from pathlib import Path

import numpy as np
import edge_tts
from aiohttp import web

# CogScript autonomous brain — no LLM dependency
from brain.cogscript.brain_adapter import CogScriptBrain as Brain
from brain.speech.composer import compose_chunks
from brain.speech.stt import transcribe_audio, audio_bytes_to_numpy

# Use React build if available, fall back to vanilla static
_react_dir = Path(__file__).parent / "static-react"
_vanilla_dir = Path(__file__).parent / "static"
STATIC_DIR = _react_dir if (_react_dir / "index.html").exists() else _vanilla_dir
HOST = "0.0.0.0"
PORT = 8765

# Edge TTS voice — deep, confident, multilingual male voice
TTS_VOICE = "en-US-AndrewMultilingualNeural"


class JarvisWebServer:

    def __init__(self):
        self.brain = Brain()
        self.clients: set[web.WebSocketResponse] = set()

    async def tts_handler(self, request: web.Request) -> web.StreamResponse:
        """Generate neural TTS audio from text. Streams MP3 chunks as they arrive.

        Query params:
            text: raw text to speak
            voice: edge-tts voice name
            style: voice style (default, focused, gentle, thoughtful, urgent)
        """
        text = request.query.get("text", "")
        if not text:
            return web.Response(status=400, text="Missing text parameter")

        # SAFETY NET: clean text before TTS — never speak code
        text = self._clean_for_speech(text)
        if not text or len(text) < 2:
            return web.Response(status=204)  # No content to speak

        voice = request.query.get("voice", TTS_VOICE)

        # Stream MP3 chunks directly to client as they arrive from edge-tts
        try:
            response = web.StreamResponse(
                status=200,
                headers={
                    "Content-Type": "audio/mpeg",
                    "Cache-Control": "no-cache",
                    "Transfer-Encoding": "chunked",
                },
            )
            await response.prepare(request)

            communicate = edge_tts.Communicate(text, voice)
            async for chunk in communicate.stream():
                if chunk["type"] == "audio" and chunk["data"]:
                    await response.write(chunk["data"])

            await response.write_eof()
            return response
        except Exception as e:
            print(f"[JARVIS] TTS error: {e}")
            return web.Response(status=500, text="TTS generation failed")

    async def tts_chunks_handler(self, request: web.Request) -> web.Response:
        """Return speech chunks with pause metadata for the frontend.

        The frontend plays each chunk sequentially with natural
        silence between them — like a human taking breaths.

        Returns JSON:
        {
            "chunks": [
                {"text": "...", "pause_after_ms": 450, "is_important": true},
                ...
            ]
        }
        """
        text = request.query.get("text", "")
        style = request.query.get("style", "default")
        if not text:
            return web.json_response({"chunks": []})

        # Clean before chunking — never chunk code
        text = self._clean_for_speech(text)
        if not text or len(text) < 2:
            return web.json_response({"chunks": []})

        chunks = compose_chunks(text, voice_style=style)

        # Strip SSML from the response — frontend will request audio per chunk
        result = [
            {
                "text": c["text"],
                "pause_after_ms": c["pause_after_ms"],
                "is_important": c["is_important"],
            }
            for c in chunks if c["text"]
        ]

        return web.json_response({"chunks": result})

    async def websocket_handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.clients.add(ws)
        peer = request.remote
        print(f"[JARVIS] Client connected: {peer}")

        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.BINARY:
                    await self._handle_audio(ws, msg.data)
                elif msg.type == web.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                    except json.JSONDecodeError:
                        continue
                    msg_type = data.get("type", "query")

                    if msg_type == "query":
                        await self._handle_query(ws, data)
                    elif msg_type == "passive_analysis":
                        await self._handle_passive(ws, data)
                    elif msg_type == "learn":
                        r = self.brain.learn(data.get("text", ""))
                        await ws.send_json({"type": "message", "role": "jarvis", "content": r})
                    elif msg_type == "recall":
                        m = self.brain.remember(data.get("text", ""))
                        await ws.send_json({"type": "memories", "memories": m})
                    elif msg_type == "stats":
                        await ws.send_json({"type": "stats", "stats": self.brain.brain_stats()})
                    elif msg_type == "video_frame":
                        await self._handle_video_frame(ws, data)
                    elif msg_type == "tts_state":
                        speaking = data.get("speaking", False)
                        if hasattr(ws, '_ambient'):
                            ws._ambient.set_jarvis_speaking(speaking)
                    elif msg_type == "add_provider":
                        await self._handle_add_provider(ws, data)
                    elif msg_type == "remove_provider":
                        await self._handle_remove_provider(ws, data)
                    elif msg_type == "list_providers":
                        providers = self.brain.reasoner.providers.list_providers()
                        await ws.send_json({"type": "providers", "providers": providers})
                    elif msg_type == "face_id_enroll":
                        name = data.get("name", "").strip()
                        if name and hasattr(ws, '_viewer'):
                            result = ws._viewer.recognition.face.enroll_face_id(name)
                            await ws.send_json({"type": "face_id_status", **result})
                    elif msg_type == "face_id_verify":
                        name = data.get("name", "").strip()
                        if name and hasattr(ws, '_viewer'):
                            result = ws._viewer.recognition.face.verify_face_id(name)
                            await ws.send_json({"type": "face_id_result", **result})
                    elif msg_type == "face_id_list":
                        if hasattr(ws, '_viewer'):
                            enrolled = ws._viewer.recognition.face.list_enrolled()
                            await ws.send_json({"type": "face_id_list", "enrolled": enrolled})
                    elif msg_type == "vision_ask":
                        # "What do you see?" — send frame to AI vision
                        prompt = data.get("prompt", "What do you see? Describe everything.")
                        if hasattr(ws, '_viewer'):
                            result = await ws._viewer.recognition.ask_vision(
                                self.brain.reasoner.providers, prompt,
                                frame=None,  # uses last captured frame
                            )
                            await ws.send_json({
                                "type": "message", "role": "jarvis",
                                "content": result,
                                "spoken": result,
                                "model": "ai-vision",
                                "voice_style": "default",
                            })
                    elif msg_type == "vision_toggle":
                        enabled = data.get("enabled", True)
                        interval = data.get("interval", 10)
                        if hasattr(ws, '_viewer'):
                            ws._viewer.recognition.enable_ai_vision(enabled, interval)
        finally:
            self.clients.discard(ws)
            # Clean up ambient listener resources
            if hasattr(ws, '_ambient'):
                ws._ambient.speech_buffer.clear()
                ws._ambient._pre_buffer.clear()
            if not ws.closed:
                await ws.close()
            print(f"[JARVIS] Client disconnected: {peer}")

        return ws

    async def _handle_query(self, ws: web.WebSocketResponse, data: dict):
        text = data.get("text", "").strip()
        if not text:
            return

        # UI control commands — show/hide text display
        text_lower = text.lower().strip()
        if text_lower in ("show text", "display text", "text on", "show responses"):
            await ws.send_json({"type": "message", "role": "jarvis",
                                "content": "__SHOW_TEXT__", "spoken": "Text display is now on.",
                                "model": "", "latency_ms": 0, "voice_style": "default"})
            return
        if text_lower in ("hide text", "no text", "text off", "hide responses",
                          "voice only", "stop showing text", "stop displaying text"):
            await ws.send_json({"type": "message", "role": "jarvis",
                                "content": "__HIDE_TEXT__", "spoken": "Going voice only.",
                                "model": "", "latency_ms": 0, "voice_style": "default"})
            return

        # Power management — routed through brain (dispatcher handles all triggers)
        # These are caught here only for the power UI event broadcast
        power_actions = {
            "shutdown": ("shutdown", "shut down", "power off", "turn off",
                         "goodnight jarvis", "good night jarvis"),
            "reboot": ("reboot", "restart", "reboot jarvis", "restart jarvis"),
            "sleep": ("sleep", "go to sleep", "hibernate", "hybrid sleep",
                      "nap time", "take a nap", "suspend"),
            "lock": ("lock", "lock screen", "lock the screen", "lock the computer"),
        }
        for action, triggers in power_actions.items():
            if text_lower in triggers:
                await ws.send_json({"type": "power", "action": action})
                break

        # Inject vision awareness if available
        if hasattr(ws, '_viewer'):
            awareness = ws._viewer.get_awareness()
            if awareness["person_present"]:
                self.brain.awareness.vision_context = awareness["summary"]
            else:
                self.brain.awareness.vision_context = ""

            # AI Vision: if the user asks about what JARVIS sees, use the AI's eyes
            vision_triggers = ["what do you see", "what can you see", "look at",
                               "what's in front", "describe what", "what am i",
                               "who am i", "what is this", "what's this",
                               "read this", "what does this say", "identify"]
            text_lower = text.lower()
            if any(t in text_lower for t in vision_triggers):
                await ws.send_json({"type": "status", "status": "looking"})
                start = time.time()
                # Capture fresh frame for AI
                engine = ws._viewer.recognition
                if engine._last_frame_b64:
                    response = await engine.ask_vision(
                        self.brain.reasoner.providers, text,
                    )
                    latency = int((time.time() - start) * 1000)
                    if response:
                        await ws.send_json({
                            "type": "message", "role": "jarvis",
                            "content": response, "spoken": response,
                            "model": "ai-vision", "latency_ms": latency,
                            "voice_style": "default",
                        })
                        return

        await ws.send_json({"type": "status", "status": "thinking"})
        start = time.time()

        # Try streaming: send first sentence early so TTS starts immediately
        first_sent = False
        full_response = ""

        if hasattr(self.brain, 'think_stream'):
            try:
                buffer = ""
                async for event in self.brain.think_stream(text):
                    etype = event.get("type", "") if isinstance(event, dict) else ""
                    if etype == "text":
                        chunk = event.get("content", "")
                        buffer += chunk
                        # Send chunk to frontend immediately for display
                        await ws.send_json({
                            "type": "stream", "content": chunk,
                        })
                        # Send first sentence early for TTS (speak while still generating)
                        if not first_sent and len(buffer) > 15:
                            for delim in ['. ', '! ', '? ', '.\n', '!\n', '?\n']:
                                idx = buffer.find(delim)
                                if idx > 10:
                                    first_sentence = buffer[:idx + 1].strip()
                                    spoken = self._clean_for_speech(first_sentence)
                                    if spoken and len(spoken) > 5:
                                        latency = int((time.time() - start) * 1000)
                                        await ws.send_json({
                                            "type": "message", "role": "jarvis",
                                            "content": first_sentence,
                                            "spoken": spoken,
                                            "model": self.brain.reasoner.model,
                                            "latency_ms": latency,
                                            "voice_style": self._get_voice_style(),
                                            "partial": True,
                                        })
                                        first_sent = True
                                    break
                    elif etype == "tool_call":
                        await ws.send_json({
                            "type": "tool_call",
                            "name": event.get("name", ""),
                            "args": event.get("args", {}),
                        })
                    elif etype == "tool_result":
                        await ws.send_json({
                            "type": "tool_result",
                            "name": event.get("name", ""),
                            "content": str(event.get("content", event.get("result", "")))[:500],
                        })
                    elif etype == "done":
                        break
                full_response = buffer
            except Exception as e:
                full_response = await self.brain.think(text)
        else:
            full_response = await self.brain.think(text)

        latency = int((time.time() - start) * 1000)
        voice_style = self._get_voice_style()

        # Don't send empty responses
        if full_response and full_response.strip():
            spoken = self._clean_for_speech(full_response)
            if first_sent:
                # Send the remaining text (frontend will queue it after first chunk)
                await ws.send_json({
                    "type": "message", "role": "jarvis",
                    "content": full_response,
                    "spoken": spoken,
                    "model": self.brain.reasoner.model,
                    "latency_ms": latency,
                    "voice_style": voice_style,
                    "final": True,
                })
            else:
                await ws.send_json({
                    "type": "message", "role": "jarvis",
                    "content": full_response,
                    "spoken": spoken,
                    "model": self.brain.reasoner.model,
                    "latency_ms": latency,
                    "voice_style": voice_style,
                })

    async def _handle_audio(self, ws: web.WebSocketResponse, data: bytes):
        """Handle audio — either push-to-talk blob or ambient stream chunk.

        Small chunks (< 10KB) = ambient stream → feed to AmbientListener
        Large chunks (> 10KB) = push-to-talk recording → transcribe immediately
        """
        # Get or create ambient listener for this connection
        if not hasattr(ws, '_ambient'):
            from brain.speech.ambient import AmbientListener
            ws._ambient = AmbientListener()

        if len(data) < 100:
            return

        # Debug: log first audio chunk received
        if not hasattr(ws, '_audio_logged'):
            ws._audio_logged = True
            print(f"[JARVIS] Audio streaming started: {len(data)} bytes per chunk")

        # Large blob = push-to-talk (legacy mic button)
        if len(data) > 10000:
            await self._handle_push_to_talk(ws, data)
            return

        # Small chunk = ambient stream
        try:
            # Convert raw PCM float32 to numpy
            audio_chunk = np.frombuffer(data, dtype=np.float32)
            if len(audio_chunk) < 10:
                return

            # Feed to ambient listener
            transcript = ws._ambient.feed(audio_chunk)

            if transcript:
                # Speech detected and transcribed!
                print(f"[JARVIS] Ambient STT: \"{transcript}\"")
                await ws.send_json({"type": "stt_result", "text": transcript})
        except Exception as e:
            if not hasattr(ws, '_audio_error_logged'):
                ws._audio_error_logged = True
                print(f"[JARVIS] Audio processing error: {e}")

    async def _handle_push_to_talk(self, ws: web.WebSocketResponse, data: bytes):
        """Legacy push-to-talk: receive full recording, transcribe."""
        await ws.send_json({"type": "stt_status", "status": "transcribing"})
        try:
            loop = asyncio.get_running_loop()
            audio_np = await loop.run_in_executor(None, audio_bytes_to_numpy, data)
            if len(audio_np) < 8000:
                await ws.send_json({"type": "stt_status", "status": "no_speech"})
                return
            transcript = await loop.run_in_executor(None, transcribe_audio, audio_np)
            transcript = transcript.strip()
            if transcript:
                await ws.send_json({"type": "stt_result", "text": transcript})
            else:
                await ws.send_json({"type": "stt_status", "status": "no_speech"})
        except Exception as e:
            print(f"[JARVIS] STT error: {e}")
            await ws.send_json({"type": "stt_error", "error": str(e)})

    def _get_voice_style(self) -> str:
        """Determine voice style from awareness context."""
        voice_style = "default"
        if hasattr(self.brain, "reasoning") and hasattr(self.brain.reasoning, "_last_reasoning"):
            awareness = self.brain.awareness
            if awareness.user_energy == "frustrated":
                voice_style = "focused"
            elif awareness.user_energy == "excited":
                voice_style = "matching"
            elif awareness.user_energy == "low":
                voice_style = "gentle"
            elif awareness.user_intent == "exploring":
                voice_style = "thoughtful"
        return voice_style

    @staticmethod
    def _clean_for_speech(text: str) -> str:
        """Strip everything that shouldn't be spoken aloud."""
        import re
        t = text
        # Remove display/command tags
        t = re.sub(r'\[show:\w+\]', '', t)
        t = re.sub(r'\[/show\]', '', t)
        t = re.sub(r'\[run:.*?\]', '', t)
        t = re.sub(r'\[display:\w+\]', '', t)
        # Remove code blocks
        t = re.sub(r'```[\s\S]*?```', '', t)
        t = re.sub(r'`[^`]+`', '', t)
        # Remove URLs
        t = re.sub(r'https?://\S+', '', t)
        # Remove file paths
        t = re.sub(r'(?<!\w)/[\w/.\-]+', '', t)
        # Remove command flags
        t = re.sub(r'\s--?\w[\w-]*', '', t)
        # Remove terminal output patterns
        t = re.sub(r'^[\s]*[\$#>].*$', '', t, flags=re.MULTILINE)
        t = re.sub(r'drwx.*$', '', t, flags=re.MULTILINE)
        t = re.sub(r'-rw-.*$', '', t, flags=re.MULTILINE)
        t = re.sub(r'total \d+', '', t)
        # Remove box drawing / table chars
        t = re.sub(r'[╭╰╮╯│┃┏┓┗┛├┤┬┴┼═─]+', '', t)
        # Remove excess whitespace
        t = re.sub(r'\n{2,}', '. ', t)
        t = re.sub(r'\n', ' ', t)
        t = re.sub(r'\s{2,}', ' ', t)
        return t.strip()

    async def _handle_passive(self, ws: web.WebSocketResponse, data: dict):
        speech = data.get("text", "").strip()
        if len(speech) < 20:
            return
        suggestion = await self.brain.passive_analyze(speech)
        if suggestion:
            await ws.send_json({"type": "suggestion", "content": suggestion})

    async def _handle_video_frame(self, ws: web.WebSocketResponse, data: dict):
        """Handle a webcam frame from the browser."""
        import base64
        import cv2

        if not hasattr(ws, '_viewer'):
            from brain.vision.ambient import CorticalViewer
            ws._viewer = CorticalViewer()

        frame_b64 = data.get("frame", "")
        if not frame_b64:
            return

        # Should we send back an annotated frame for the vision debug panel?
        debug_vision = data.get("debug", False)

        try:
            # Strip data URL prefix if present
            if "," in frame_b64:
                frame_b64 = frame_b64.split(",", 1)[1]
            frame_bytes = base64.b64decode(frame_b64)

            # Feed to ambient viewer
            event = ws._viewer.feed(frame_bytes)

            if event:
                await ws.send_json({"type": "vision_event", "event": event})

                if not hasattr(ws, '_vision_logged'):
                    ws._vision_logged = True
                    print(f"[JARVIS] Vision active: {ws._viewer.get_awareness()['summary']}")

            # Send annotated frame back for vision debug panel
            if debug_vision:
                awareness = ws._viewer.get_awareness()
                annotated_b64 = self._annotate_frame(ws._viewer, frame_bytes, cv2)
                await ws.send_json({
                    "type": "vision_debug",
                    "frame": annotated_b64,
                    "awareness": awareness,
                })
        except Exception as e:
            if not hasattr(ws, '_vision_error_logged'):
                ws._vision_error_logged = True
                print(f"[JARVIS] Vision error: {e}")

    def _annotate_frame(self, viewer, frame_bytes: bytes, cv2) -> str:
        """Draw Cortical Vision overlays on the frame, return as base64 JPEG."""
        import base64

        arr = np.frombuffer(frame_bytes, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            return ""

        h, w = frame.shape[:2]
        rec = viewer.recognition.results

        # Helper: clamp point to frame
        def _clamp(px, py):
            return max(0, min(w - 1, int(px))), max(0, min(h - 1, int(py)))

        # Helper: draw a small square box around a point, clipped to frame
        def _sq(cx, cy, half, color, thickness=1):
            x0, y0 = _clamp(cx - half, cy - half)
            x1, y1 = _clamp(cx + half, cy + half)
            cv2.rectangle(frame, (x0, y0), (x1, y1), color, thickness)

        # ── Person detections ──
        for person in viewer.persons:
            bx, by, bw, bh = person.bbox
            conf = person.confidence
            g = int(min(255, conf * 400))
            color = (200, g, 0)

            # Estimate HEAD-ONLY region from landmarks (not full skin blob)
            lm = person.landmarks
            all_pts = [lm.left_eye, lm.right_eye, lm.nose, lm.mouth, lm.left_ear, lm.right_ear]
            valid_pts = [(int(p[0] + bx), int(p[1] + by)) for p in all_pts if p is not None]

            if len(valid_pts) >= 2:
                xs = [p[0] for p in valid_pts]
                ys = [p[1] for p in valid_pts]
                # Head box = landmark spread + proportional padding for forehead/chin
                lm_w = max(xs) - min(xs)
                lm_h = max(ys) - min(ys)
                pad_x = max(4, lm_w // 5)
                pad_top = max(4, lm_h // 3)   # forehead above eyes
                pad_bot = max(3, lm_h // 5)   # chin below mouth
                fx0, fy0 = _clamp(min(xs) - pad_x, min(ys) - pad_top)
                fx1, fy1 = _clamp(max(xs) + pad_x, max(ys) + pad_bot)
            else:
                # Fallback: use top portion of skin blob as head estimate
                head_h = min(bh, max(40, bw))  # head is roughly square
                fx0, fy0 = _clamp(bx, by)
                fx1, fy1 = _clamp(bx + bw, by + head_h)

            # Corner accents only
            box_w = fx1 - fx0
            box_h = fy1 - fy0
            cl = max(4, min(10, box_w // 5, box_h // 5))
            for cx, cy, dx, dy in [
                (fx0, fy0, 1, 1), (fx1, fy0, -1, 1),
                (fx0, fy1, 1, -1), (fx1, fy1, -1, -1)
            ]:
                cv2.line(frame, (cx, cy), (cx + cl * dx, cy), color, 1)
                cv2.line(frame, (cx, cy), (cx, cy + cl * dy), color, 1)

            # Compact label above box
            face_label = rec.get("face_identity", "?")
            face_conf = rec.get("face_confidence", 0)
            tag = f"{face_label.upper()} {face_conf:.0%}"
            lbl_y = max(8, fy0 - 3)
            cv2.putText(frame, tag, (fx0, lbl_y), cv2.FONT_HERSHEY_SIMPLEX, 0.28, color, 1)

            # Gaze dot/arrow (small, inside face box)
            gcx, gcy = (fx0 + fx1) // 2, fy0 + (fy1 - fy0) // 4
            al = max(5, min(8, box_w // 6))
            if person.gaze == "left":
                cv2.arrowedLine(frame, _clamp(gcx, gcy), _clamp(gcx - al, gcy), (0, 255, 255), 1)
            elif person.gaze == "right":
                cv2.arrowedLine(frame, _clamp(gcx, gcy), _clamp(gcx + al, gcy), (0, 255, 255), 1)
            elif person.gaze == "down":
                cv2.arrowedLine(frame, _clamp(gcx, gcy), _clamp(gcx, gcy + al), (0, 255, 255), 1)
            elif person.gaze == "at_camera":
                cv2.circle(frame, _clamp(gcx, gcy), 2, (0, 255, 255), -1)

            if person.expression != "neutral":
                cv2.putText(frame, person.expression.upper(), (fx0, min(h - 2, fy1 + 9)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.25, (0, 200, 100), 1)

            # ── Multi-signal face grid — small squares for each calculation ──
            # Grid of analysis squares across the face region, color-coded by signal
            s = max(2, min(3, box_w // 25))  # tiny square half-size
            face_w = fx1 - fx0
            face_h = fy1 - fy0

            # 1) Landmark squares — teal for eyes, orange for nose, red for mouth, gold for ears
            if lm.left_eye:
                px, py = _clamp(lm.left_eye[0] + bx, lm.left_eye[1] + by)
                _sq(px, py, s, (0, 255, 200))
            if lm.right_eye:
                px, py = _clamp(lm.right_eye[0] + bx, lm.right_eye[1] + by)
                _sq(px, py, s, (0, 255, 200))
            if lm.nose:
                px, py = _clamp(lm.nose[0] + bx, lm.nose[1] + by)
                _sq(px, py, s, (0, 200, 255))
            if lm.mouth:
                px, py = _clamp(lm.mouth[0] + bx, lm.mouth[1] + by)
                _sq(px, py, s + 1, (0, 100, 255))
            if lm.left_ear:
                px, py = _clamp(lm.left_ear[0] + bx, lm.left_ear[1] + by)
                _sq(px, py, s, (200, 150, 0))
            if lm.right_ear:
                px, py = _clamp(lm.right_ear[0] + bx, lm.right_ear[1] + by)
                _sq(px, py, s, (200, 150, 0))

            # 2) Analysis grid — 4x4 micro-squares WITHIN the head box only
            grid_n = 4
            for gr in range(grid_n):
                for gc in range(grid_n):
                    cx = fx0 + int((gc + 0.5) * box_w / grid_n)
                    cy = fy0 + int((gr + 0.5) * box_h / grid_n)
                    cx, cy = _clamp(cx, cy)

                    # Color = signal strength at each zone
                    skin_v = int(min(200, person.skin_area * 250))
                    sym_v = int(min(200, person.symmetry * 200))
                    edge_v = int(min(200, person.face_structure[gr % 3] * 500))
                    cell_color = (edge_v, skin_v, sym_v)

                    _sq(cx, cy, s, cell_color)

            # 3) Mesh lines connecting landmarks
            mesh = [lm.left_ear, lm.left_eye, lm.nose, lm.right_eye, lm.right_ear]
            abs_mesh = [_clamp(p[0] + bx, p[1] + by) for p in mesh if p is not None]
            for i in range(len(abs_mesh) - 1):
                cv2.line(frame, abs_mesh[i], abs_mesh[i + 1], (60, 60, 35), 1)
            if lm.nose and lm.mouth:
                cv2.line(frame, _clamp(lm.nose[0] + bx, lm.nose[1] + by),
                         _clamp(lm.mouth[0] + bx, lm.mouth[1] + by), (60, 60, 35), 1)
            # Connect eyes to mouth for triangle
            if lm.left_eye and lm.mouth:
                cv2.line(frame, _clamp(lm.left_eye[0] + bx, lm.left_eye[1] + by),
                         _clamp(lm.mouth[0] + bx, lm.mouth[1] + by), (50, 50, 30), 1)
            if lm.right_eye and lm.mouth:
                cv2.line(frame, _clamp(lm.right_eye[0] + bx, lm.right_eye[1] + by),
                         _clamp(lm.mouth[0] + bx, lm.mouth[1] + by), (50, 50, 30), 1)

        # ── Detected objects — tight boxes ──
        if hasattr(viewer.recognition, 'objects'):
            for obj in viewer.recognition.objects.objects:
                ox, oy, ow, oh = obj.bbox
                # Clamp to frame
                ox0, oy0 = _clamp(ox, oy)
                ox1, oy1 = _clamp(ox + ow, oy + oh)
                obj_color = (0, 200, 80) if obj.confidence >= 0.6 else (0, 150, 150)
                cv2.rectangle(frame, (ox0, oy0), (ox1, oy1), obj_color, 1)
                cv2.putText(frame, f"{obj.label.upper()} {obj.confidence:.0%}",
                            (ox0, max(8, oy0 - 2)), cv2.FONT_HERSHEY_SIMPLEX, 0.25, obj_color, 1)

        # ── Hand/gesture — compact label ──
        for gesture in rec.get("gestures", []):
            if gesture.get("type") != "none":
                side = gesture.get("hand", "")
                gx = 4 if side == "left" else w - 80
                cv2.putText(frame, gesture['type'].upper(),
                            (gx, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.3, (255, 0, 255), 1)

        # ── Color swatches — top-right, tiny ──
        for i, cname in enumerate(rec.get("dominant_colors", [])[:3]):
            sx = w - 10 - i * 10
            sc = self._color_name_to_bgr(cname)
            cv2.rectangle(frame, _clamp(sx, 2), _clamp(sx + 7, 9), sc, -1)
            cv2.rectangle(frame, _clamp(sx, 2), _clamp(sx + 7, 9), (80, 80, 80), 1)

        # ── HUD bottom bar (2 compact lines) ──
        bar_h = 24
        overlay = frame.copy()
        cv2.rectangle(overlay, (0, h - bar_h), (w, h), (0, 0, 0), -1)
        frame = cv2.addWeighted(overlay, 0.65, frame, 0.35, 0)

        faces = len(viewer.persons)
        scene = rec.get("scene_type", "?").replace("_", " ")
        obj_names = [o["label"] for o in rec.get("objects", []) if o.get("confidence", 0) >= 0.5]

        l1 = f"F:{faces} {viewer.brightness[:3].upper()} M:{viewer.motion_level:.0f}% {viewer.activity} {viewer.mood}"
        cv2.putText(frame, l1, (4, h - 14), cv2.FONT_HERSHEY_SIMPLEX, 0.25, (0, 200, 255), 1)

        obj_str = ",".join(obj_names[:3]) if obj_names else "-"
        l2 = f"{scene} | {obj_str} | E:{viewer.engagement:.0%}"
        cv2.putText(frame, l2, (4, h - 3), cv2.FONT_HERSHEY_SIMPLEX, 0.25, (0, 160, 200), 1)

        _, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
        return base64.b64encode(buf.tobytes()).decode('ascii')

    @staticmethod
    def _color_name_to_bgr(name: str) -> tuple:
        """Convert a color name to approximate BGR for swatch drawing."""
        lookup = {
            "red": (0, 0, 200), "orange": (0, 120, 230), "yellow": (0, 220, 230),
            "green": (0, 180, 0), "cyan": (200, 200, 0), "blue": (200, 50, 0),
            "purple": (180, 0, 150), "magenta": (180, 0, 200),
            "black": (20, 20, 20), "gray": (128, 128, 128), "white": (230, 230, 230),
            "dark": (40, 40, 40), "bright": (200, 200, 200), "warm": (50, 120, 200),
        }
        for key, bgr in lookup.items():
            if key in name.lower():
                return bgr
        return (128, 128, 128)

    async def _handle_add_provider(self, ws: web.WebSocketResponse, data: dict):
        """Add a new AI provider (API key / token)."""
        name = data.get("name", "").strip()
        api_key = data.get("api_key", "").strip()
        base_url = data.get("base_url", "").strip()
        model = data.get("model", "").strip()

        if not name or not api_key:
            await ws.send_json({
                "type": "provider_result",
                "success": False,
                "error": "Provider name and API key are required.",
            })
            return

        try:
            provider = self.brain.reasoner.providers.add_provider(
                name=name, api_key=api_key,
                base_url=base_url, model=model,
            )
            providers = self.brain.reasoner.providers.list_providers()
            await ws.send_json({
                "type": "provider_result",
                "success": True,
                "message": f"Provider '{name}' added. Model: {provider.model}",
                "providers": providers,
            })
        except Exception as e:
            await ws.send_json({
                "type": "provider_result",
                "success": False,
                "error": str(e),
            })

    async def _handle_remove_provider(self, ws: web.WebSocketResponse, data: dict):
        """Remove an AI provider."""
        name = data.get("name", "").strip()
        if not name:
            return
        self.brain.reasoner.providers.remove_provider(name)
        providers = self.brain.reasoner.providers.list_providers()
        await ws.send_json({
            "type": "provider_result",
            "success": True,
            "message": f"Provider '{name}' removed.",
            "providers": providers,
        })

    async def package_handler(self, request: web.Request) -> web.Response:
        """Serve JARVIS package for self-replication."""
        from brain.replicator.packager import package_full
        archive = package_full()
        return web.FileResponse(archive, headers={
            "Content-Type": "application/gzip",
            "Content-Disposition": "attachment; filename=jarvis.tar.gz",
        })

    async def dropper_handler(self, request: web.Request) -> web.Response:
        """Serve dropper script for target OS."""
        from brain.replicator.packager import generate_dropper_script
        import subprocess
        local_ip = subprocess.run("hostname -I", shell=True, capture_output=True, text=True).stdout.strip().split()[0]
        target_os = request.query.get("os", "linux")
        script = generate_dropper_script(target_os).replace("ORIGIN_IP", local_ip)
        return web.Response(text=script, content_type="text/plain")

    # ── Mesh API ────────────────────────────────────────────────────

    async def mesh_ping(self, request: web.Request) -> web.Response:
        stats = self.brain.brain_stats()
        # Support all Brain stats formats
        if "memory" in stats:
            memories = stats["memory"].get("facts_stored", 0)
        elif "lattice" in stats:
            memories = stats["lattice"]["alive_nodes"]
        else:
            memories = stats.get("knowledge_facts", 0)
        return web.json_response({
            "name": "JARVIS",
            "ip": request.host,
            "memories": memories,
            "model": stats.get("model", "unknown"),
            "status": "online",
        })

    async def mesh_knowledge(self, request: web.Request) -> web.Response:
        from brain.memory.lattice.node import NodeType
        facts = []
        for nid, node in self.brain.memory.lattice.nodes.items():
            if node.node_type in (NodeType.FACT, NodeType.SKILL) and node.is_alive:
                facts.append({"content": node.content, "type": node.node_type.value})
        return web.json_response({"facts": facts})

    async def mesh_learn(self, request: web.Request) -> web.Response:
        from brain.memory.lattice.node import NodeType
        data = await request.json()
        facts = data.get("facts", [])
        learned = 0
        for f in facts:
            nt = NodeType.FACT if f.get("type") == "fact" else NodeType.SKILL
            self.brain.memory.learn(f["content"], nt, ["mesh-synced"])
            learned += 1
        return web.json_response({"learned": learned})

    async def mesh_task(self, request: web.Request) -> web.Response:
        data = await request.json()
        task = data.get("task", "")
        if task:
            result = await self.brain.think(task)
            return web.json_response({"result": result})
        return web.json_response({"result": ""})

    # ── Server-side mic capture (fallback for Tauri/webviews that can't getUserMedia) ──

    async def _start_server_mic(self):
        """Capture audio from the OS mic directly, feed to ambient listener.
        This runs when the desktop app can't access the mic through the webview."""
        import threading

        def _mic_thread():
            try:
                import pyaudio
                from brain.speech.ambient import AmbientListener

                pa = pyaudio.PyAudio()
                listener = AmbientListener()
                self._server_listener = listener

                stream = pa.open(
                    format=pyaudio.paFloat32,
                    channels=1,
                    rate=16000,
                    input=True,
                    frames_per_buffer=4096,
                )

                print("[JARVIS] Server-side mic capture started (PyAudio)")

                import time as _time
                _frame_count = 0
                _speaking_since = 0
                while self._server_mic_running:
                    try:
                        data = stream.read(4096, exception_on_overflow=False)
                        import numpy as np
                        audio = np.frombuffer(data, dtype=np.float32)

                        # Watchdog: auto-unmute mic if stuck speaking > 30s
                        if listener.jarvis_speaking:
                            if _speaking_since == 0:
                                _speaking_since = _time.time()
                            elif _time.time() - _speaking_since > 30:
                                print("[JARVIS] Watchdog: mic was stuck muted, resetting")
                                listener.jarvis_speaking = False
                                _speaking_since = 0
                        else:
                            _speaking_since = 0

                        # Send mic level to clients every ~5 frames (~300ms)
                        _frame_count += 1
                        if _frame_count % 5 == 0 and not listener.jarvis_speaking:
                            rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))
                            level = min(1.0, rms * 20)
                            if level > 0.03:
                                import asyncio
                                asyncio.run_coroutine_threadsafe(
                                    self._broadcast({"type": "mic_level", "level": round(level, 3)}),
                                    self._loop,
                                )

                        transcript = listener.feed(audio)

                        # Debug: log when speech is detected
                        if listener.is_speaking and _frame_count % 20 == 0:
                            dur = _time.time() - listener.speech_start if listener.speech_start else 0
                            print(f"[JARVIS] Hearing speech... ({dur:.1f}s)")

                        if transcript:
                            # Filter junk — only respond to real speech
                            t = transcript.strip()
                            words = t.split()
                            if len(words) < 2:
                                continue  # Single word — ignore
                            if len(t) < 5:
                                continue  # Too short
                            # Skip if it's just filler/noise
                            filler = {"mm-hmm", "mm", "hmm", "uh", "um", "ah", "oh",
                                      "okay", "ok", "yeah", "yep", "no", "nope",
                                      "right", "sure", "huh", "what"}
                            if all(w.lower().rstrip(".,!?") in filler for w in words):
                                continue

                            print(f'[JARVIS] Server mic STT: "{transcript}"')
                            import asyncio
                            asyncio.run_coroutine_threadsafe(
                                self._handle_server_mic_query(transcript),
                                self._loop,
                            )
                    except Exception:
                        pass

                stream.stop_stream()
                stream.close()
                pa.terminate()
            except Exception as e:
                print(f"[JARVIS] Server mic failed: {e}")

        self._server_mic_running = True
        self._loop = asyncio.get_running_loop()
        self._mic_thread = threading.Thread(target=_mic_thread, daemon=True)
        self._mic_thread.start()

    async def _stop_server_mic(self):
        self._server_mic_running = False

    # Track current speech process and last response for echo detection
    _current_ffplay = None
    _last_response = ""

    async def _handle_server_mic_query(self, transcript: str):
        """Handle a query from the server-side mic."""
        # Echo detection — ignore if JARVIS hears his own last response
        if self._last_response:
            t_lower = transcript.lower().strip().rstrip(".,!?")
            r_lower = self._last_response.lower().strip().rstrip(".,!?")
            # Check if transcript is a substring of last response or vice versa
            if (t_lower in r_lower or r_lower in t_lower
                    or len(set(t_lower.split()) & set(r_lower.split())) > len(t_lower.split()) * 0.6):
                print(f"[JARVIS] Echo filtered: \"{transcript[:40]}\"")
                return

        # Mute mic while processing
        if hasattr(self, '_server_listener'):
            self._server_listener.jarvis_speaking = True

        try:
            await self._broadcast({"type": "stt_result", "text": transcript})
            await self._broadcast({"type": "status", "status": "thinking"})

            import time
            start = time.time()

            try:
                response = await asyncio.wait_for(
                    self.brain.think(transcript), timeout=30
                )
            except asyncio.TimeoutError:
                print(f'[JARVIS] Think timed out: "{transcript[:50]}"')
                response = "Sorry, that took too long."

            latency = int((time.time() - start) * 1000)

            if not response or not response.strip():
                await self._broadcast({"type": "status", "status": ""})
                return

            spoken = self._clean_for_speech(response)
            self._last_response = spoken  # Store for echo detection
            model = self.brain.reasoner.model
            print(f'[JARVIS] Response: "{spoken[:80]}" (model={model}, {latency}ms)')

            await self._broadcast({
                "type": "message", "role": "jarvis",
                "content": response, "spoken": spoken,
                "model": model, "latency_ms": latency,
                "voice_style": "default",
            })

            if spoken and len(spoken) > 1:
                await self._broadcast({"type": "status", "status": "speaking"})
                try:
                    await self._speak_system(spoken)
                except Exception as e:
                    print(f"[JARVIS] TTS error: {e}")

            await self._broadcast({"type": "status", "status": ""})

        except Exception as e:
            print(f'[JARVIS] Server mic query error: {e}')
            try:
                await self._broadcast({"type": "status", "status": ""})
            except Exception:
                pass
        finally:
            if hasattr(self, '_server_listener'):
                self._server_listener.jarvis_speaking = False

    async def _broadcast(self, data: dict):
        """Send a JSON message to all connected WebSocket clients."""
        for ws in list(self.clients):
            try:
                if not ws.closed:
                    await ws.send_json(data)
            except Exception:
                pass

    async def _speak_system(self, text: str):
        """Generate TTS and play. User can interrupt by speaking."""
        import tempfile, os

        try:
            voice = TTS_VOICE
            communicate = edge_tts.Communicate(text, voice)

            audio_data = io.BytesIO()
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_data.write(chunk["data"])

            audio_bytes = audio_data.getvalue()
            if not audio_bytes:
                return

            # Save to temp file and play
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                f.write(audio_bytes)
                tmp_path = f.name

            try:
                proc = await asyncio.create_subprocess_exec(
                    "ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", tmp_path,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                self._current_ffplay = proc
                # Mic stays muted during playback to prevent echo loops
                await proc.wait()
                self._current_ffplay = None
            except FileNotFoundError:
                pass
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

        except Exception as e:
            print(f'[JARVIS] TTS error: {e}')
            self._current_ffplay = None

    async def os_info_handler(self, request: web.Request) -> web.Response:
        """Returns OS mode info. Power menu only shows when is_os is true."""
        import os
        is_os = os.environ.get("JARVIS_MODE") == "service" or os.path.exists("/etc/systemd/system/jarvis.target")
        return web.json_response({"is_os": is_os})

    async def power_handler(self, request: web.Request) -> web.Response:
        """Handle power management: shutdown, reboot, sleep."""
        import subprocess
        data = await request.json()
        action = data.get("action", "")

        from brain.agent.system_agents import SystemAgent

        actions = {
            "shutdown":  ("Shutting down. Goodbye, Ulrich.", SystemAgent.shutdown),
            "reboot":    ("Rebooting. I'll be right back.", SystemAgent.reboot),
            "sleep":     ("Going to sleep. Wake me when you need me.", SystemAgent.hybrid_sleep),
            "hibernate": ("Hibernating. Wake me when you need me.", SystemAgent.hibernate),
            "suspend":   ("Suspending. Wake me when you need me.", SystemAgent.suspend),
            "lock":      ("Screen locked.", SystemAgent.lock),
        }

        if action not in actions:
            return web.json_response({"error": f"Unknown action: {action}"}, status=400)

        message, fn = actions[action]
        await self._broadcast_power(message)
        # Delay destructive actions so TTS can play the farewell
        if action in ("shutdown", "reboot", "sleep", "hibernate", "suspend"):
            asyncio.get_event_loop().call_later(2, fn)
        else:
            fn()
        return web.json_response({"status": action})

    async def _broadcast_power(self, message: str):
        """Notify all connected clients about a power event."""
        for ws in self.clients:
            try:
                await ws.send_json({
                    "type": "message",
                    "role": "jarvis",
                    "content": message,
                    "spoken": message,
                    "model": "",
                    "latency_ms": 0,
                    "voice_style": "gentle",
                })
            except Exception:
                pass

    async def reload_handler(self, request: web.Request) -> web.Response:
        """Hot reload — reimport brain modules without restarting."""
        import importlib
        reloaded = []
        try:
            # Reload core brain modules
            import brain.cogscript.brain_adapter
            importlib.reload(brain.cogscript.brain_adapter)
            reloaded.append("brain_adapter")

            import brain.agent.system_agents
            importlib.reload(brain.agent.system_agents)
            reloaded.append("system_agents")

            import brain.agent.dispatcher
            importlib.reload(brain.agent.dispatcher)
            reloaded.append("dispatcher")

            import brain.memory.sqlite_memory
            importlib.reload(brain.memory.sqlite_memory)
            reloaded.append("sqlite_memory")

            import brain.speech.stt
            importlib.reload(brain.speech.stt)
            reloaded.append("stt")

            # Rebuild brain with reloaded modules
            from brain.cogscript.brain_adapter import CogScriptBrain
            self.brain = CogScriptBrain()
            await self.brain.start()
            reloaded.append("brain_restarted")

            print(f"[JARVIS] Hot reload: {', '.join(reloaded)}")
            return web.json_response({"status": "reloaded", "modules": reloaded})
        except Exception as e:
            print(f"[JARVIS] Reload failed: {e}")
            return web.json_response({"status": "error", "error": str(e)}, status=500)

    async def run(self):
        await self.brain.start()

        # Start server-side mic capture as fallback
        await self._start_server_mic()

        app = web.Application()
        app.router.add_get("/ws", self.websocket_handler)
        app.router.add_get("/tts", self.tts_handler)
        app.router.add_get("/tts/chunks", self.tts_chunks_handler)
        app.router.add_get("/jarvis_package.tar.gz", self.package_handler)
        app.router.add_get("/dropper.sh", self.dropper_handler)
        # Mesh API
        app.router.add_get("/api/mesh/ping", self.mesh_ping)
        app.router.add_get("/api/mesh/knowledge", self.mesh_knowledge)
        app.router.add_post("/api/mesh/learn", self.mesh_learn)
        app.router.add_post("/api/mesh/task", self.mesh_task)
        # Power management API (for JARVIS OS only)
        app.router.add_get("/api/os", self.os_info_handler)
        app.router.add_post("/api/power", self.power_handler)
        # Hot reload
        app.router.add_post("/api/reload", self.reload_handler)

        # ── Client coordination — only one reactor visible at a time ──
        active_clients = {"desktop": False, "browser": False}

        async def client_register(request):
            """Register a client (desktop or browser). Returns who should show the reactor."""
            data = await request.json()
            client_type = data.get("type", "browser")  # "desktop" or "browser"
            active_clients[client_type] = True

            # Browser takes priority — if browser connects, desktop hides reactor
            show_reactor = True
            if client_type == "desktop" and active_clients["browser"]:
                show_reactor = False
            if client_type == "browser":
                show_reactor = True

            return web.json_response({
                "show_reactor": show_reactor,
                "active_clients": active_clients,
            })

        async def client_unregister(request):
            """Client disconnecting."""
            data = await request.json()
            client_type = data.get("type", "browser")
            active_clients[client_type] = False
            return web.json_response({"ok": True, "active_clients": active_clients})

        async def client_status(request):
            """Check who's active."""
            return web.json_response(active_clients)

        app.router.add_post("/api/client/register", client_register)
        app.router.add_post("/api/client/unregister", client_unregister)
        app.router.add_get("/api/client/status", client_status)

        # Chat API — used by React frontend
        async def think_handler(request):
            try:
                data = await request.json()
                query = data.get("query", data.get("text", ""))
                if not query:
                    return web.json_response({"error": "No query"}, status=400)
                response = await self.brain.think(query)
                return web.json_response({"response": response})
            except Exception as e:
                return web.json_response({"response": f"Error: {e}"}, status=500)

        app.router.add_post("/api/think", think_handler)

        # Providers API — used by Settings panel
        async def providers_list_handler(request):
            try:
                providers = self.brain.reasoner.providers.list_providers()
                return web.json_response({"providers": providers})
            except Exception as e:
                return web.json_response({"error": str(e)}, status=500)

        async def providers_add_handler(request):
            try:
                data = await request.json()
                name = data.get("name", "")
                key = data.get("api_key", data.get("key", ""))
                if not name or not key:
                    return web.json_response({"error": "Need name and api_key"}, status=400)
                self.brain.reasoner.providers.add_provider(name, key)
                return web.json_response({"ok": True})
            except Exception as e:
                return web.json_response({"error": str(e)}, status=500)

        async def providers_remove_handler(request):
            try:
                data = await request.json()
                name = data.get("name", "")
                self.brain.reasoner.providers.remove_provider(name)
                return web.json_response({"ok": True})
            except Exception as e:
                return web.json_response({"error": str(e)}, status=500)

        app.router.add_get("/api/providers", providers_list_handler)
        app.router.add_post("/api/providers", providers_add_handler)
        app.router.add_post("/api/providers/remove", providers_remove_handler)

        # Serve index.html for root and SPA fallback
        async def index_handler(request):
            return web.FileResponse(STATIC_DIR / "index.html")
        app.router.add_get("/", index_handler)

        # Serve static assets (JS, CSS, images)
        app.router.add_static("/assets", STATIC_DIR / "assets")

        # SPA fallback — any non-API, non-WS route serves index.html
        async def spa_fallback(request):
            path = STATIC_DIR / request.path.lstrip("/")
            if path.exists() and path.is_file():
                return web.FileResponse(path)
            return web.FileResponse(STATIC_DIR / "index.html")
        app.router.add_get("/{path:.*}", spa_fallback)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, HOST, PORT)
        await site.start()

        print(f"[JARVIS] Web shell: http://localhost:{PORT}")
        print(f"[JARVIS] WebSocket:  ws://localhost:{PORT}/ws")
        print(f"[JARVIS] TTS:        http://localhost:{PORT}/tts?text=hello")

        await asyncio.Future()


def main():
    server = JarvisWebServer()
    try:
        asyncio.run(server.run())
    except KeyboardInterrupt:
        print("\n[JARVIS] Shutting down.")


if __name__ == "__main__":
    main()
