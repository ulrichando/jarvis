"""JARVIS Web Shell — HTTP + WebSocket + Neural TTS server.

Serves the local React frontend, handles local WebSocket clients,
and provides remote session management via the bridge/remote subsystems
so JARVIS can operate as a cloud-capable service.
"""

import asyncio
import json
import logging
import os
import re
import secrets
import signal
import time
import io
import uuid
from pathlib import Path

import numpy as np
import edge_tts
from aiohttp import web

# Full JARVIS Brain with agent loop, tools
from src.brain import Brain
from src.speech.composer import compose_chunks
from src.speech.stt import transcribe_audio, audio_bytes_to_numpy

# Remote session management
from src.remote.RemoteSessionManager import RemoteSessionManager
from src.remote.session_manager import get_remote_session_manager, set_remote_session_manager
from src.remote.remotePermissionBridge import RemotePermissionBridge
from src.remote.sdkMessageAdapter import to_sdk_message, from_sdk_message

# Bridge config and types
from src.bridge.bridgeConfig import get_bridge_access_token, get_bridge_base_url, get_remote_config
from src.bridge.types import BridgeConfig
from src.bridge.bridgeEnabled import is_bridge_enabled
from src.bridge.bridgeMessaging import (
    BoundedUUIDSet,
    handle_ingress_message,
    is_eligible_bridge_message,
)

logger = logging.getLogger(__name__)

# Use React build if available, fall back to vanilla static
_react_dir = Path(__file__).parent / "static-react"
_vanilla_dir = Path(__file__).parent / "static"
STATIC_DIR = _react_dir if (_react_dir / "index.html").exists() else _vanilla_dir
HOST = "0.0.0.0"
PORT = 8765

# Edge TTS voice — deep, confident, multilingual male voice
TTS_VOICE = "en-US-AndrewMultilingualNeural"


class JarvisWebServer:

    # --- Security: allowed WebSocket origins ---
    _ALLOWED_ORIGINS = {
        "http://localhost", "http://127.0.0.1", "http://0.0.0.0",
        "https://localhost", "https://127.0.0.1",
    }
    # Rate limit: max messages per client per second
    _WS_RATE_LIMIT = 10  # messages/sec
    _WS_RATE_WINDOW = 1.0  # seconds

    def __init__(self):
        self.brain = None  # Deferred — initialized in run() after port binds
        self.clients: set[web.WebSocketResponse] = set()
        self._ws_rate: dict = {}  # ws -> (count, window_start)
        # Remote session manager — shared singleton
        remote_config = get_remote_config()
        self.remote_manager = RemoteSessionManager(
            max_sessions=remote_config.get("max_sessions", 5),
        )
        set_remote_session_manager(self.remote_manager)
        self.remote_permission_bridge = RemotePermissionBridge()
        # Auth token for remote API (None = no auth required)
        self._remote_auth_token: str | None = remote_config.get("auth_token")
        # Local auth token (optional, from JARVIS_WS_TOKEN env or config)
        self._local_auth_token: str | None = os.environ.get("JARVIS_WS_TOKEN") or remote_config.get("ws_token")

    def _check_ws_origin(self, request: web.Request) -> bool:
        """Validate WebSocket origin header against allowed origins."""
        origin = request.headers.get("Origin", "")
        if not origin:
            return True  # No origin = direct connection (curl, desktop app)
        # Strip port for comparison
        origin_base = re.sub(r':\d+$', '', origin)
        if origin_base in self._ALLOWED_ORIGINS:
            return True
        # Allow same-host connections
        host = request.headers.get("Host", "")
        if host and origin.endswith(host.split(":")[0]):
            return True
        logging.getLogger("jarvis.web").warning("Rejected WS from origin: %s", origin)
        return False

    def _check_ws_rate(self, ws: web.WebSocketResponse) -> bool:
        """Simple rate limiter per WebSocket connection."""
        now = time.time()
        ws_id = id(ws)
        count, window_start = self._ws_rate.get(ws_id, (0, now))
        if now - window_start > self._WS_RATE_WINDOW:
            self._ws_rate[ws_id] = (1, now)
            return True
        if count >= self._WS_RATE_LIMIT:
            return False
        self._ws_rate[ws_id] = (count + 1, window_start)
        return True

    def _init_brain(self):
        """Initialize Brain (heavy — MCP servers take ~25s)."""
        self.brain = Brain(quiet=True)
        # Pre-load Whisper model so first voice request is fast
        try:
            from src.speech.stt import _get_model
            _get_model()
        except Exception:
            pass

    # Lazy-loaded Piper voice (local, fast)
    _piper_voice = None

    def _get_piper_voice(self):
        if self._piper_voice is None:
            try:
                from piper import PiperVoice
                model_path = os.path.expanduser("~/.local/share/piper-voices/en_US-lessac-medium.onnx")
                if os.path.exists(model_path):
                    self._piper_voice = PiperVoice.load(model_path)
                    print("[JARVIS] Piper TTS loaded (local, fast)")
            except Exception as e:
                print(f"[JARVIS] Piper TTS unavailable: {e}")
        return self._piper_voice

    async def tts_handler(self, request: web.Request) -> web.StreamResponse:
        """Generate TTS audio from text. Uses Piper (local, fast) with Edge TTS fallback.

        Query params:
            text: raw text to speak
            voice: edge-tts voice name (only used for Edge TTS fallback)
            engine: "piper" or "edge" (default: piper if available)
        """
        text = request.query.get("text", "")
        if not text:
            return web.Response(status=400, text="Missing text parameter")

        text = self._clean_for_speech(text)
        if not text or len(text) < 2:
            return web.Response(status=204)

        engine = request.query.get("engine", "edge")

        # Try Piper first (local, ~0.1s latency)
        if engine != "edge":
            piper = self._get_piper_voice()
            if piper:
                try:
                    import io, wave
                    loop = asyncio.get_running_loop()

                    def _generate():
                        buf = io.BytesIO()
                        with wave.open(buf, 'wb') as wf:
                            wf.setnchannels(1)
                            wf.setsampwidth(2)
                            wf.setframerate(piper.config.sample_rate)
                            for chunk in piper.synthesize(text):
                                wf.writeframes(chunk.audio_int16_bytes)
                        return buf.getvalue()

                    wav_data = await loop.run_in_executor(None, _generate)
                    return web.Response(
                        body=wav_data,
                        content_type="audio/wav",
                        headers={"Cache-Control": "no-cache"},
                    )
                except Exception as e:
                    print(f"[JARVIS] Piper TTS failed, falling back to Edge: {e}")

        # Fallback: Edge TTS (cloud, ~1-2s latency)
        voice = request.query.get("voice", TTS_VOICE)
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

    async def _safe_send(self, ws, data: dict):
        """Send JSON to a WebSocket, silently handle disconnects."""
        try:
            if not ws.closed:
                await ws.send_json(data)
        except (ConnectionResetError, ConnectionError, RuntimeError, Exception):
            pass  # Client disconnected — not an error

    MAX_CLIENTS = 15

    async def websocket_handler(self, request: web.Request) -> web.WebSocketResponse:
        # Origin validation — reject cross-origin connections
        if not self._check_ws_origin(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            await ws.send_json({"type": "error", "error": "Origin not allowed"})
            await ws.close(code=1008, message=b"Origin not allowed")
            return ws

        # Optional local auth token check
        if self._local_auth_token:
            token = request.query.get("token", "")
            if token != self._local_auth_token:
                ws = web.WebSocketResponse()
                await ws.prepare(request)
                await ws.send_json({"type": "error", "error": "Unauthorized"})
                await ws.close(code=1008, message=b"Unauthorized")
                return ws

        # Connection limit
        if len(self.clients) >= self.MAX_CLIENTS:
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            await ws.send_json({"type": "error", "error": "Too many connections"})
            await ws.close(code=1013, message=b"Try again later")
            return ws

        ws = web.WebSocketResponse(heartbeat=20.0, max_msg_size=16 * 1024 * 1024)
        await ws.prepare(request)
        self.clients.add(ws)
        peer = request.remote
        print(f"[JARVIS] Client connected: {peer} ({len(self.clients)} total)")

        # If brain is already ready, tell the new client immediately
        if self.brain is not None:
            await self._safe_send(ws, {
                "type": "brain_ready",
                "tools": len(self.brain.mcp.get_tool_schemas()) + 40,
            })

        try:
            async for msg in ws:
                try:
                    # Rate limiting
                    if not self._check_ws_rate(ws):
                        await self._safe_send(ws, {"type": "error", "error": "Rate limit exceeded"})
                        continue

                    if msg.type == web.WSMsgType.BINARY:
                        await self._handle_audio(ws, msg.data)
                    elif msg.type == web.WSMsgType.TEXT:
                        try:
                            data = json.loads(msg.data)
                        except json.JSONDecodeError:
                            continue
                        msg_type = data.get("type", "query")

                    if msg_type == "query":
                        # Always try command interception first (camera/restart/etc work without brain)
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
                        # Also mute/unmute the server-side mic listener
                        if hasattr(self, '_server_listener'):
                            self._server_listener.jarvis_speaking = speaking
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
                except Exception as _msg_err:
                    # Individual message handling failed — log and continue
                    print(f"[JARVIS] Message error: {_msg_err}")
                    continue
        finally:
            self.clients.discard(ws)
            self._ws_rate.pop(id(ws), None)  # Clean up rate limit state
            # Clean up all custom attributes to prevent memory leaks
            for attr in ('_ambient', '_viewer', '_audio_logged', '_vision_logged', '_init_warned'):
                if hasattr(ws, attr):
                    obj = getattr(ws, attr)
                    if hasattr(obj, 'speech_buffer'):
                        obj.speech_buffer.clear()
                    if hasattr(obj, '_pre_buffer'):
                        obj._pre_buffer.clear()
                    try: delattr(ws, attr)
                    except: pass
            try:
                if not ws.closed:
                    await ws.close()
            except Exception:
                pass
            print(f"[JARVIS] Client disconnected: {peer} ({len(self.clients)} remaining)")

        return ws

    async def _handle_query(self, ws: web.WebSocketResponse, data: dict):
        text = data.get("text", "").strip()
        if not text:
            return

        # Voice input — mark as ambient so the LLM knows it came from the mic
        is_ambient = data.get("ambient", False)
        if is_ambient:
            # Prefix with voice context so the LLM can decide how to respond
            text = f"[voice input] {text}"
            print(f'[Ulrich] "{text[14:54]}"')
        else:
            print(f'[JARVIS] Browser query: "{text[:80]}"')

        # Normalize for voice-friendly matching (Whisper adds punctuation/filler)
        text_lower = text.lower().strip()
        import re as _re_match
        text_clean = _re_match.sub(r'[^\w\s]', '', text_lower).strip()

        # UI control commands — show/hide text display
        _show_text = ("show text", "display text", "text on", "show responses")
        _hide_text = ("hide text", "no text", "text off", "hide responses",
                      "voice only", "stop showing text", "stop displaying text")
        if text_clean in _show_text or text_lower in _show_text:
            await ws.send_json({"type": "message", "role": "jarvis",
                                "content": "__SHOW_TEXT__",
                                "model": "", "latency_ms": 0})
            return
        if text_clean in _hide_text or text_lower in _hide_text:
            await ws.send_json({"type": "message", "role": "jarvis",
                                "content": "__HIDE_TEXT__",
                                "model": "", "latency_ms": 0})
            return

        # Face recognition enrollment: "remember my face", "learn my face"
        _face_enroll = ("remember my face", "learn my face", "save my face",
                        "remember me", "enroll my face")
        if any(p in text_clean for p in _face_enroll):
            # Try enrolling via active camera stream first
            for client_ws in self.clients:
                if hasattr(client_ws, '_viewer'):
                    result = client_ws._viewer.recognition.face.enroll_face_id("Ulrich")
                    # Also capture IR frame for better face ID
                    try:
                        from src.vision.camera import has_ir_camera, capture_ir_frame
                        if has_ir_camera():
                            ir_path = capture_ir_frame()
                            if ir_path:
                                result["ir_captured"] = True
                    except Exception:
                        pass
                    msg = result.get("message", "Face enrollment started.")
                    if result.get("ir_captured"):
                        msg += " IR camera detected — using infrared for better accuracy."
                    await ws.send_json({"type": "message", "role": "jarvis",
                                        "content": msg,
                                        "model": "", "latency_ms": 0})
                    return
            # No camera stream — try direct IR capture
            try:
                from src.vision.camera import has_ir_camera
                if has_ir_camera():
                    # Turn on camera and start enrollment
                    await self._broadcast({"type": "camera", "enabled": True})
                    await ws.send_json({"type": "message", "role": "jarvis",
                                        "content": "IR camera detected. Turning on camera for face enrollment. Look at the camera.",
                                        "model": "", "latency_ms": 0})
                    return
            except Exception:
                pass
            await ws.send_json({"type": "message", "role": "jarvis",
                                "content": "Camera needs to be on first. Say 'turn on camera'.",
                                "model": "", "latency_ms": 0})
            return

        # Provider setup — open the wizard
        _setup_triggers = ("setup", "provider setup", "add provider", "add model",
                           "setup providers", "configure ai", "change model",
                           "switch model", "model setup", "open setup",
                           "settings", "provider settings", "ai settings")
        if text_clean in _setup_triggers or any(p in text_clean for p in _setup_triggers):
            await self._broadcast({"type": "provider_error", "manual": True})
            await ws.send_json({"type": "message", "role": "jarvis",
                                "content": "Opening provider setup.",
                                "model": "", "latency_ms": 0})
            return

        # Camera on/off
        _cam_on = ("turn on camera", "camera on", "enable camera", "open camera",
                   "start camera", "turn on webcam", "webcam on")
        _cam_off = ("turn off camera", "camera off", "disable camera", "close camera",
                    "stop camera", "turn off webcam", "webcam off")
        if text_clean in _cam_on or any(p in text_clean for p in _cam_on):
            await self._broadcast({"type": "camera", "enabled": True})
            await ws.send_json({"type": "message", "role": "jarvis",
                                "content": "Camera is on.",
                                "model": "", "latency_ms": 0})
            return
        if text_clean in _cam_off or any(p in text_clean for p in _cam_off):
            await self._broadcast({"type": "camera", "enabled": False})
            await ws.send_json({"type": "message", "role": "jarvis",
                                "content": "Camera is off.",
                                "model": "", "latency_ms": 0})
            return

        # Restart command
        _restart_triggers = ("restart", "restart yourself", "restart jarvis",
                             "reboot yourself", "jarvis restart", "reload yourself")
        if text_clean in _restart_triggers or text_lower in _restart_triggers \
                or any(p in text_clean for p in _restart_triggers):
            await ws.send_json({"type": "message", "role": "jarvis",
                                "content": "Restarting...",
                                "model": "", "latency_ms": 0})
            print("[JARVIS] Restart requested via voice/text")
            await asyncio.sleep(1)
            # Graceful shutdown — close all clients, kill desktop, then exit
            for c in list(self.clients):
                try: await c.close()
                except: pass
            import subprocess as _sp_kill
            _sp_kill.run(["pkill", "-f", "src.desktop.app"], capture_output=True)
            # Use signal to trigger clean aiohttp shutdown
            import signal
            os.kill(os.getpid(), signal.SIGTERM)
            return

        # UI handoff — voice-friendly: strip punctuation, match substrings
        switch_to_desktop = ("switch to desktop", "go to desktop", "move to desktop",
                             "desktop mode", "jarvis desktop", "back to desktop")
        switch_to_browser = ("switch to browser", "go to browser", "move to browser",
                             "open in browser", "browser mode", "jarvis browser",
                             "open browser")
        if text_clean in switch_to_desktop or text_lower in switch_to_desktop \
                or any(p in text_clean for p in switch_to_desktop):
            clients = getattr(self, '_active_clients', {})
            await self._broadcast({"type": "handoff", "target": "desktop"})
            clients["browser"] = False
            # Launch desktop app if not already running and display available
            if not clients.get("desktop") and os.environ.get("DISPLAY"):
                import subprocess as _sp_dt
                _jarvis_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                env = {**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0.0")}
                _sp_dt.Popen(
                    ["python3", "-c", "from src.desktop.app import main; main()"],
                    cwd=_jarvis_root, start_new_session=True,
                    stdout=_sp_dt.DEVNULL, stderr=_sp_dt.DEVNULL, env=env,
                )
            await ws.send_json({"type": "message", "role": "jarvis",
                                "content": "Moving to desktop.",
                                "model": "", "latency_ms": 0, "voice_style": "default"})
            return
        if text_clean in switch_to_browser or text_lower in switch_to_browser \
                or any(p in text_clean for p in switch_to_browser):
            import subprocess as _sp
            env = {**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0.0")}
            _sp.Popen(["xdg-open", f"http://127.0.0.1:{PORT}/"], start_new_session=True,
                      stdout=_sp.DEVNULL, stderr=_sp.DEVNULL, env=env)
            await ws.send_json({"type": "message", "role": "jarvis",
                                "content": "Opening browser.",
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
            if text_clean in triggers or text_lower in triggers \
                    or any(t in text_clean for t in triggers):
                await ws.send_json({"type": "power", "action": action})
                break

        # Guard: Brain still loading — commands above work without it, LLM needs it
        if self.brain is None:
            await ws.send_json({
                "type": "message", "role": "jarvis",
                "content": "Still initializing... give me a moment.",
                "model": "", "latency_ms": 0,
            })
            return

        # Inject vision awareness if available
        if hasattr(ws, '_viewer'):
            awareness = ws._viewer.get_awareness()
            if awareness["person_present"]:
                parts = [awareness["summary"]]
                # Add face identity if recognized
                identity = awareness.get("identity") or awareness.get("name")
                if identity:
                    parts.append(f"Identified as: {identity}")
                expression = awareness.get("expression")
                if expression and expression != "neutral":
                    parts.append(f"Expression: {expression}")
                gaze = awareness.get("gaze_direction")
                if gaze:
                    parts.append(f"Looking: {gaze}")
                self.brain.awareness.vision_context = ". ".join(parts)
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
        first_spoken_end = 0  # track where first spoken chunk ended
        full_response = ""
        used_tools = False  # track if agent loop used any tools
        tool_id_counter = 0  # unique IDs for tool call/result pairing
        current_tool_id = None  # track the current tool's ID
        narration_task = None  # track background narration so we can cancel it
        early_tts_task = None  # track first-sentence TTS so we can wait for it

        if hasattr(self.brain, 'think_stream'):
            try:
                buffer = ""
                # Only accumulate text from the LAST LLM turn for speech
                # (after all tools finish, the final LLM reply is what matters)
                speech_buffer = ""
                async for event in self.brain.think_stream(text):
                    etype = event.get("type", "") if isinstance(event, dict) else ""
                    if etype == "text":
                        chunk = event.get("content", "")
                        buffer += chunk
                        speech_buffer += chunk
                        # Send chunk to frontend immediately for display
                        await ws.send_json({
                            "type": "stream", "content": chunk,
                        })
                        # Send first sentence early for TTS (speak while still generating)
                        if not first_sent and not used_tools and len(buffer) > 8:
                            for delim in ['. ', '! ', '? ', '.\n', '!\n', '?\n', ', ', '— ', ': ']:
                                idx = buffer.find(delim)
                                if idx > 5:
                                    first_sentence = buffer[:idx + 1].strip()
                                    spoken = self._clean_for_speech(first_sentence)
                                    if spoken and len(spoken) > 5:
                                        latency = int((time.time() - start) * 1000)
                                        # Speak first sentence immediately for voice queries
                                        _early_server_tts = False
                                        if is_voice:
                                            clients = getattr(self, '_active_clients', {})
                                            if clients.get("desktop") and not clients.get("browser"):
                                                early_tts_task = asyncio.create_task(self._speak_system(spoken))
                                                _early_server_tts = True
                                        await ws.send_json({
                                            "type": "message", "role": "jarvis",
                                            "content": first_sentence,
                                            "spoken": "" if _early_server_tts else spoken,
                                            "model": self.brain.reasoner.model,
                                            "latency_ms": latency,
                                            "voice_style": self._get_voice_style(),
                                            "partial": True,
                                            "server_tts": _early_server_tts,
                                        })
                                        first_sent = True
                                        first_spoken_end = idx + 1
                                    break
                    elif etype == "tool_call":
                        tool_name = event.get("name", "")
                        # Voice narration — speak what we're doing on first tool call
                        if is_voice and not used_tools:
                            _narr = {
                                "bash": "Let me check.",
                                "read_file": "Reading that.",
                                "write_file": "Writing that.",
                                "edit_file": "Editing that.",
                                "search_files": "Searching.",
                                "web_search": "Looking that up.",
                                "web_fetch": "Fetching that.",
                                "dispatch": "On it.",
                            }.get(tool_name, "Working on it.")
                            clients = getattr(self, '_active_clients', {})
                            if clients.get("desktop") and not clients.get("browser"):
                                narration_task = asyncio.create_task(self._speak_short(_narr))
                        used_tools = True
                        # Reset speech buffer — only speak the LLM's final reply
                        speech_buffer = ""
                        tool_id_counter += 1
                        current_tool_id = f"tool-{tool_id_counter}"
                        print(f"[JARVIS] Tool: {tool_name}({str(event.get('args', {}))[:80]})")
                        await ws.send_json({
                            "type": "tool_call",
                            "id": current_tool_id,
                            "name": tool_name,
                            "args": event.get("args", {}),
                        })
                    elif etype == "tool_result":
                        result_str = str(event.get("content", event.get("result", "")))[:500]
                        print(f"[JARVIS] Result: {event.get('name', '')} → {result_str[:80]}")
                        await ws.send_json({
                            "type": "tool_result",
                            "id": current_tool_id,
                            "name": event.get("name", ""),
                            "content": result_str,
                        })
                        current_tool_id = None
                    elif etype == "usage":
                        await ws.send_json({
                            "type": "usage",
                            "input_tokens": event.get("input_tokens", 0),
                            "output_tokens": event.get("output_tokens", 0),
                            "context_pct": event.get("context_pct", 0),
                            "context_used": event.get("context_used", 0),
                            "context_max": event.get("context_max", 0),
                            "session_cost": event.get("session_cost", ""),
                        })
                    elif etype == "done":
                        # Send final context status
                        if event.get("context_status"):
                            await ws.send_json({
                                "type": "context_status",
                                "status": event.get("context_status", ""),
                                "pct": event.get("context_pct", 0),
                            })
                        break
                full_response = buffer
            except Exception as e:
                full_response = await self.brain.think(text)
                speech_buffer = full_response
        else:
            full_response = await self.brain.think(text)
            speech_buffer = full_response

        latency = int((time.time() - start) * 1000)
        voice_style = self._get_voice_style()

        # Check if all providers failed — notify frontend to show setup wizard
        try:
            from src.server import _provider_error
            if _provider_error.get("failed"):
                await ws.send_json({"type": "provider_error", "errors": _provider_error.get("errors", [])})
                _provider_error["failed"] = False  # Reset after notifying
        except ImportError:
            pass

        # Never leave voice input unanswered
        is_voice = text.startswith("[voice input]")
        if (not full_response or not full_response.strip()) and is_voice:
            full_response = "Sorry, I didn't catch that. Say again?"
            speech_buffer = full_response

        if full_response and full_response.strip():
            model = getattr(self.brain.reasoner, 'active_model_name', '') or getattr(self.brain.reasoner, 'model', '')
            print(f'[JARVIS] Response: "{full_response.strip()[:80]}" (model={model}, {latency}ms)')

            # For speech: use only the final LLM turn (after tools)
            if used_tools:
                spoken = self._clean_for_speech(speech_buffer)
            else:
                spoken = self._clean_for_speech(full_response)

            # Pre-mute server mic before TTS plays (avoid race with tts_state)
            if spoken and len(spoken) > 3 and hasattr(self, '_server_listener'):
                self._server_listener.jarvis_speaking = True

            # Check if server will handle TTS (suppress frontend TTS to avoid double voice)
            _clients = getattr(self, '_active_clients', {})
            _server_tts = (is_voice and spoken and len(spoken) > 3
                           and _clients.get("desktop") and not _clients.get("browser"))
            _sent_spoken = "" if _server_tts else spoken

            if first_sent:
                await ws.send_json({
                    "type": "message", "role": "jarvis",
                    "content": full_response,
                    "spoken": _sent_spoken,
                    "model": self.brain.reasoner.model,
                    "latency_ms": latency,
                    "voice_style": voice_style,
                    "final": True,
                    "server_tts": _server_tts,
                })
            else:
                await ws.send_json({
                    "type": "message", "role": "jarvis",
                    "content": full_response,
                    "spoken": _sent_spoken,
                    "model": self.brain.reasoner.model,
                    "latency_ms": latency,
                    "voice_style": voice_style,
                    "server_tts": _server_tts,
                })

            # Server-side TTS — only for voice input, not typed text
            if _server_tts:
                    # Cancel any in-progress narration before speaking the real response
                    if narration_task and not narration_task.done():
                        narration_task.cancel()
                        if hasattr(self, '_current_ffplay') and self._current_ffplay:
                            try: self._current_ffplay.kill()
                            except: pass
                        await asyncio.sleep(0.1)
                    # Wait for early first-sentence TTS to finish before speaking remainder
                    if early_tts_task and not early_tts_task.done():
                        try:
                            await asyncio.wait_for(early_tts_task, timeout=15)
                        except Exception:
                            pass
                    # If first sentence already spoken, speak only the remainder
                    if first_sent and first_spoken_end > 0:
                        remainder = self._clean_for_speech(spoken[first_spoken_end:].strip())
                        if remainder and len(remainder) > 3:
                            try:
                                await asyncio.wait_for(self._speak_system(remainder), timeout=30)
                            except Exception as e:
                                print(f"[JARVIS] Server TTS error: {e}")
                    else:
                        try:
                            await asyncio.wait_for(self._speak_system(spoken), timeout=30)
                        except Exception as e:
                            print(f"[JARVIS] Server TTS error: {e}")

    async def _handle_audio(self, ws: web.WebSocketResponse, data: bytes):
        """Handle audio — either push-to-talk blob or ambient stream chunk.

        Small chunks (< 10KB) = ambient stream → feed to AmbientListener
        Large chunks (> 10KB) = push-to-talk recording → transcribe immediately
        """
        # Get or create ambient listener for this connection
        if not hasattr(ws, '_ambient'):
            from src.speech.ambient import AmbientListener
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
        # Skip if server mic is already handling audio (prevents duplicate processing)
        if getattr(self, '_server_mic_running', False):
            return

        try:
            # Convert raw PCM float32 to numpy
            audio_chunk = np.frombuffer(data, dtype=np.float32)
            if len(audio_chunk) < 10:
                return

            # Feed to ambient listener
            transcript = ws._ambient.feed(audio_chunk)

            if transcript:
                # Echo detection: compare to last response to avoid JARVIS hearing himself
                if hasattr(self, '_last_response') and self._last_response:
                    last_words = set(self._last_response.lower().split())
                    heard_words = set(transcript.lower().split())
                    if last_words and heard_words:
                        overlap = len(last_words & heard_words) / max(len(heard_words), 1)
                        if overlap > 0.5:
                            print(f"[JARVIS] Echo detected, ignoring: \"{transcript[:60]}\"")
                            return

                print(f"[Ulrich] \"{transcript}\"")
                await ws.send_json({"type": "stt_result", "text": transcript})

                # Process the transcription — send to brain like a voice query
                await self._handle_query(ws, {
                    "text": transcript,
                    "ambient": True,
                })
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
        """Strip everything that shouldn't be spoken aloud.

        Aggressively removes code blocks, file paths, terminal output,
        and anything that sounds unnatural when read by TTS.
        """
        t = text
        # Remove display/command tags
        t = re.sub(r'\[show:\w+\]', '', t)
        t = re.sub(r'\[/show\]', '', t)
        t = re.sub(r'\[run:.*?\]', '', t)
        t = re.sub(r'\[display:\w+\]', '', t)
        # Remove fenced code blocks (greedy — catches nested/multi-line)
        t = re.sub(r'```[a-z]*\n[\s\S]*?```', '', t)
        t = re.sub(r'```[\s\S]*?```', '', t)
        # Remove inline code
        t = re.sub(r'`[^`]*`', '', t)
        # Remove indented code blocks (4+ spaces or tab at line start)
        t = re.sub(r'^(    |\t).*$', '', t, flags=re.MULTILINE)
        # Remove lines that look like code
        t = re.sub(r'^.*(?:import |from .* import |def |class |return |if __|elif |except |async def |await |self\.).*$', '', t, flags=re.MULTILINE)
        t = re.sub(r'^\s*\w+\s*[=:]\s*.{10,}$', '', t, flags=re.MULTILINE)
        t = re.sub(r'^\s*(?:const |let |var |function |=>).*$', '', t, flags=re.MULTILINE)
        # Remove lines with programming syntax
        t = re.sub(r'^.*[{}\[\]();]+.*$', '', t, flags=re.MULTILINE)
        t = re.sub(r'^\s*[<>/]+.*$', '', t, flags=re.MULTILINE)
        # Remove markdown headers, bold, italic, links
        t = re.sub(r'^#{1,6}\s+', '', t, flags=re.MULTILINE)
        t = re.sub(r'\*{1,2}([^*]+)\*{1,2}', r'\1', t)
        t = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', t)
        # Remove URLs
        t = re.sub(r'https?://\S+', '', t)
        # Remove file paths (src/foo/bar.py, ./path, ~/path, /abs/path)
        t = re.sub(r'(?<!\w)[~/.]?/?(?:src|test|lib|node_modules|dist|build|\.?\w+)/[\w/.\-]+', '', t)
        t = re.sub(r'(?<!\w)/[\w/.\-]{5,}', '', t)
        # Remove command flags and CLI-like content
        t = re.sub(r'\s--?\w[\w-]*(?:=\S+)?', '', t)
        t = re.sub(r'^\s*\$\s+.*$', '', t, flags=re.MULTILINE)
        # Remove JSON/dict-like blocks
        t = re.sub(r'\{[^}]{10,}\}', '', t)
        t = re.sub(r'\[[^\]]{20,}\]', '', t)
        # Remove terminal output patterns
        t = re.sub(r'^[\s]*[\$#>].*$', '', t, flags=re.MULTILINE)
        t = re.sub(r'drwx.*$', '', t, flags=re.MULTILINE)
        t = re.sub(r'-rw-.*$', '', t, flags=re.MULTILINE)
        t = re.sub(r'total \d+', '', t)
        # Remove stack traces and error dumps
        t = re.sub(r'^\s*(File |Traceback|at |Error:|Exception:|TypeError|ValueError|KeyError|ImportError).*$', '', t, flags=re.MULTILINE)
        # Remove pip/npm output
        t = re.sub(r'^\s*(Requirement|Successfully|Collecting|Downloading|Installing).*$', '', t, flags=re.MULTILINE)
        # Remove box drawing / table chars
        t = re.sub(r'[╭╰╮╯│┃┏┓┗┛├┤┬┴┼═─|]+', '', t)
        # Remove bullet list markers
        t = re.sub(r'^\s*[-*•]\s+', '', t, flags=re.MULTILINE)
        # Remove numbered list markers
        t = re.sub(r'^\s*\d+\.\s+', '', t, flags=re.MULTILINE)
        # Limit length — don't speak novels
        t = t[:600]
        # Clean up whitespace
        t = re.sub(r'\n{2,}', '. ', t)
        t = re.sub(r'\n', ' ', t)
        t = re.sub(r'\s{2,}', ' ', t)
        t = re.sub(r'\.\s*\.', '.', t)
        t = re.sub(r'^[\s.,;:]+', '', t)
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
            from src.vision.ambient import CorticalViewer
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

            # Store latest frame for the `see` tool
            import time as _tf
            from src.server import _latest_camera_frame
            _latest_camera_frame["frame"] = frame_b64
            _latest_camera_frame["timestamp"] = _tf.time()

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
        from src.replicator.packager import package_full
        archive = package_full()
        return web.FileResponse(archive, headers={
            "Content-Type": "application/gzip",
            "Content-Disposition": "attachment; filename=jarvis.tar.gz",
        })

    async def dropper_handler(self, request: web.Request) -> web.Response:
        """Serve dropper script for target OS."""
        from src.replicator.packager import generate_dropper_script
        import subprocess
        local_ip = subprocess.run(["hostname", "-I"], capture_output=True, text=True).stdout.strip().split()[0]
        target_os = request.query.get("os", "linux")
        # Whitelist valid OS values to prevent injection
        if target_os not in ("linux", "macos", "windows", "darwin"):
            return web.Response(text="Invalid OS", status=400)
        script = generate_dropper_script(target_os).replace("ORIGIN_IP", local_ip)
        return web.Response(text=script, content_type="text/plain")

    # ── Mesh API ────────────────────────────────────────────────────

    async def _transcribe_handler(self, request: web.Request) -> web.Response:
        """Transcribe audio sent from CLI or other clients."""
        try:
            reader = await request.multipart()
            field = await reader.next()
            audio_bytes = await field.read()

            audio_np = audio_bytes_to_numpy(audio_bytes)
            text = transcribe_audio(audio_np, 16000)
            return web.json_response({"text": text or ""})
        except Exception as e:
            return web.json_response({"text": "", "error": str(e)}, status=500)

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
        from src.memory.lattice.node import NodeType
        facts = []
        for nid, node in self.brain.memory.lattice.nodes.items():
            if node.node_type in (NodeType.FACT, NodeType.SKILL) and node.is_alive:
                facts.append({"content": node.content, "type": node.node_type.value})
        return web.json_response({"facts": facts})

    async def mesh_learn(self, request: web.Request) -> web.Response:
        from src.memory.lattice.node import NodeType
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
        Only starts if hardware has a microphone. Skips entirely when UI handles voice."""
        # Hardware check: skip if no mic available
        try:
            from src.hardware import detect_hardware
            hw = detect_hardware()
            if not hw.can_voice_input:
                print("[JARVIS] No microphone detected — server mic disabled")
                return
        except Exception:
            pass

        import threading

        def _mic_thread():
            """Server mic with auto-recovery on device failure."""
            import pyaudio, numpy as np, time as _time
            from src.speech.ambient import AmbientListener

            MAX_RETRIES = 5
            retry_delay = 1.0

            for attempt in range(MAX_RETRIES):
                pa = None
                stream = None
                try:
                    pa = pyaudio.PyAudio()
                    listener = AmbientListener()
                    self._server_listener = listener

                    stream = pa.open(
                        format=pyaudio.paFloat32, channels=1, rate=16000,
                        input=True, frames_per_buffer=4096,
                    )
                    print(f"[JARVIS] Server mic started (attempt {attempt + 1})")
                    retry_delay = 1.0  # Reset on success

                    _frame_count = 0
                    while self._server_mic_running:
                        try:
                            data = stream.read(4096, exception_on_overflow=False)
                            audio = np.frombuffer(data, dtype=np.float32)

                            if listener.jarvis_speaking:
                                continue

                            _frame_count += 1
                            if _frame_count % 5 == 0:
                                rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))
                                level = min(1.0, rms * 20)
                                if level > 0.03:
                                    asyncio.run_coroutine_threadsafe(
                                        self._broadcast({"type": "mic_level", "level": round(level, 3)}),
                                        self._loop,
                                    )

                            transcript = listener.feed(audio)
                            if listener.is_speaking and _frame_count % 20 == 0:
                                dur = _time.time() - listener.speech_start if listener.speech_start else 0
                                print(f"[JARVIS] Hearing speech... ({dur:.1f}s)")

                            if transcript:
                                t = transcript.strip()
                                words = t.split()
                                if len(words) < 2 or len(t) < 5:
                                    continue
                                filler = {"mm-hmm", "mm", "hmm", "uh", "um", "ah", "oh",
                                          "okay", "ok", "yeah", "yep", "no", "nope",
                                          "right", "sure", "huh", "what"}
                                if all(w.lower().rstrip(".,!?") in filler for w in words):
                                    continue
                                # Face gate — only respond to the owner
                                if self._face_gate_enabled:
                                    is_owner = self._verify_owner_face()
                                    if not is_owner:
                                        print(f'[JARVIS] Face gate BLOCKED: "{transcript[:40]}" (not owner)')
                                        continue

                                # Log with speaker name from CorticalViewer face recognition
                                _speaker = "Ulrich"
                                try:
                                    for _ws in list(self.clients):
                                        _v = getattr(_ws, '_viewer', None)
                                        if _v and _v.recognition.face.current_identity:
                                            _lbl = _v.recognition.face.get_label(_v.recognition.face.current_identity)
                                            if _lbl and _lbl not in ("unknown", "none"):
                                                _speaker = _lbl.capitalize()
                                            break
                                except Exception:
                                    pass
                                print(f'[{_speaker}] "{transcript}"')
                                asyncio.run_coroutine_threadsafe(
                                    self._handle_server_mic_query(transcript), self._loop)
                        except Exception as e:
                            if "Input overflowed" not in str(e):
                                print(f"[JARVIS] Mic read error: {e}")

                except Exception as e:
                    print(f"[JARVIS] Server mic error (attempt {attempt + 1}/{MAX_RETRIES}): {e}")
                finally:
                    if stream:
                        try: stream.stop_stream(); stream.close()
                        except: pass
                    if pa:
                        try: pa.terminate()
                        except: pass

                if not self._server_mic_running:
                    break
                _time.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 30.0)

            if self._server_mic_running:
                print("[JARVIS] Server mic permanently failed after retries")

        self._server_mic_running = True
        self._loop = asyncio.get_running_loop()
        self._mic_thread = threading.Thread(target=_mic_thread, daemon=True)
        self._mic_thread.start()

    async def _stop_server_mic(self):
        self._server_mic_running = False

    # Track current speech process and last response for echo detection
    _current_ffplay = None
    _last_response = ""

    # Face gate — only respond to voice from the verified owner
    _face_gate_enabled = True

    def _verify_owner_face(self) -> bool:
        """Check if the owner is present using the CorticalViewer's live face recognition.

        Uses the already-running vision pipeline on the desktop WebSocket connection,
        which continuously processes camera frames and tracks identities.

        Fail-open: returns True if no viewer, no face data, or any error.
        """
        try:
            # Find a connected client with an active CorticalViewer
            for ws in list(self.clients):
                viewer = getattr(ws, '_viewer', None)
                if viewer is None:
                    continue

                face_rec = viewer.recognition.face
                identity_id = face_rec.current_identity
                if identity_id is None:
                    continue  # no face currently detected

                label = face_rec.get_label(identity_id)
                confidence = face_rec.current_confidence

                if label in ("primary_user", "ulrich", "Ulrich", "owner") and confidence >= 0.5:
                    return True

                # Face detected but not the owner
                print(f"[JARVIS] Face gate: label={label}, confidence={confidence:.2f}")
                return False

            # No viewer or no face data — fail-open
            return True

        except Exception as e:
            print(f"[JARVIS] Face gate error (allowing): {e}")
            return True

    async def _handle_server_mic_query(self, transcript: str):
        """Handle a query from the server-side mic."""
        # Echo detection — ignore if JARVIS hears his own last response
        # Only filter near-exact echoes. The mic muting handles most echo prevention;
        # this is a last resort for audio that leaks through the cooldown window.
        if self._last_response:
            t_lower = transcript.lower().strip().rstrip(".,!?")
            r_lower = self._last_response.lower().strip().rstrip(".,!?")
            # Exact substring match (JARVIS repeated back verbatim)
            if t_lower in r_lower or r_lower in t_lower:
                print(f"[JARVIS] Echo filtered (substring): \"{transcript[:40]}\"")
                return
            # High word overlap on short transcripts only (likely partial echo, not conversation)
            heard_words = set(t_lower.split())
            if len(heard_words) <= 8:
                overlap = len(heard_words & set(r_lower.split())) / max(len(heard_words), 1)
                if overlap > 0.7:
                    print(f"[JARVIS] Echo filtered ({overlap:.0%} overlap): \"{transcript[:40]}\"")
                    return

        # Mute mic while processing
        if hasattr(self, '_server_listener'):
            self._server_listener.jarvis_speaking = True

        try:
            # Voice command interception — same matching as _handle_query
            import re as _re_mic
            text_lower = transcript.lower().strip()
            text_clean = _re_mic.sub(r'[^\w\s]', '', text_lower).strip()

            # Face gate toggle
            if any(p in text_clean for p in ("face lock on", "enable face lock", "face gate on",
                                              "lock to my face", "only respond to me")):
                self._face_gate_enabled = True
                await self._broadcast({"type": "message", "role": "jarvis",
                    "content": "Face lock enabled. I'll only respond to you now.",
                    "model": "", "latency_ms": 0, "voice_style": "default"})
                clients = getattr(self, '_active_clients', {})
                if clients.get("desktop") and not clients.get("browser"):
                    asyncio.create_task(self._speak_short("Face lock on."))
                return
            if any(p in text_clean for p in ("face lock off", "disable face lock", "face gate off",
                                              "respond to anyone", "respond to everyone")):
                self._face_gate_enabled = False
                await self._broadcast({"type": "message", "role": "jarvis",
                    "content": "Face lock disabled. I'll respond to anyone now.",
                    "model": "", "latency_ms": 0, "voice_style": "default"})
                clients = getattr(self, '_active_clients', {})
                if clients.get("desktop") and not clients.get("browser"):
                    asyncio.create_task(self._speak_short("Face lock off."))
                return

            switch_to_desktop = ("switch to desktop", "go to desktop", "move to desktop",
                                 "desktop mode", "jarvis desktop", "back to desktop")
            switch_to_browser = ("switch to browser", "go to browser", "move to browser",
                                 "open in browser", "browser mode", "jarvis browser",
                                 "open browser")
            if text_clean in switch_to_desktop or any(p in text_clean for p in switch_to_desktop):
                clients = getattr(self, '_active_clients', {})
                await self._broadcast({"type": "handoff", "target": "desktop"})
                clients["browser"] = False
                if not clients.get("desktop"):
                    import subprocess as _sp_dt2
                    _jarvis_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                    env = {**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0.0")}
                    _sp_dt2.Popen(
                        ["python3", "-c", "from src.desktop.app import main; main()"],
                        cwd=_jarvis_root, start_new_session=True,
                        stdout=_sp_dt2.DEVNULL, stderr=_sp_dt2.DEVNULL, env=env,
                    )
                await self._broadcast({
                    "type": "message", "role": "jarvis",
                    "content": "Moving to desktop.",
                    "model": "", "latency_ms": 0, "voice_style": "default",
                })
                return
            if text_clean in switch_to_browser or any(p in text_clean for p in switch_to_browser):
                import subprocess as _sp
                env = {**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0.0")}
                _sp.Popen(["xdg-open", f"http://127.0.0.1:{PORT}/"], start_new_session=True,
                          stdout=_sp.DEVNULL, stderr=_sp.DEVNULL, env=env)
                await self._broadcast({
                    "type": "message", "role": "jarvis",
                    "content": "Opening browser.",
                    "model": "", "latency_ms": 0, "voice_style": "default",
                })
                return

            await self._broadcast({"type": "stt_result", "text": transcript})

            # Guard: Brain still loading
            if self.brain is None:
                await self._broadcast({
                    "type": "message", "role": "jarvis",
                    "content": "Still initializing... give me a moment.",
                    "model": "", "latency_ms": 0, "voice_style": "default",
                })
                return

            await self._broadcast({"type": "status", "status": "thinking"})

            import time
            start = time.time()

            # Narrate tool calls via voice so user knows what JARVIS is doing
            _tool_count = [0]
            _loop = asyncio.get_event_loop()
            _narrated = [False]  # Only speak the first narration

            def _on_tool(name, args):
                _tool_count[0] += 1
                print(f"[JARVIS] Tool: {name}({str(args)[:80]})")
                _desc = {
                    "bash": "Let me check",
                    "read_file": "Reading that file",
                    "write_file": "Writing the file",
                    "edit_file": "Editing the file",
                    "search_files": "Searching for that",
                    "web_search": "Looking that up",
                    "web_fetch": "Fetching that page",
                    "dispatch": "On it",
                }.get(name, "Working on it")
                # Speak on first tool call only — don't interrupt with repeated narration
                if not _narrated[0]:
                    _narrated[0] = True
                    asyncio.run_coroutine_threadsafe(
                        self._speak_short(_desc), _loop)

            def _on_result(name, result):
                print(f"[JARVIS] Result: {name} → {str(result)[:80]}")

            try:
                response = await asyncio.wait_for(
                    self.brain.think(f"[voice input] {transcript}",
                                    on_tool_call=_on_tool, on_tool_result=_on_result),
                    timeout=120  # 2 min — tool-heavy tasks (scans, builds) need more time
                )
            except asyncio.TimeoutError:
                print(f'[JARVIS] Think timed out: "{transcript[:50]}"')
                response = "That's taking a while — still working on it."

            latency = int((time.time() - start) * 1000)

            if not response or not response.strip():
                # Voice input should ALWAYS get a response — never go silent
                response = "Done."
                # If tools were called but no text returned, the action likely succeeded

            spoken = self._clean_for_speech(response)
            self._last_response = spoken  # Store for echo detection
            model = self.brain.reasoner.model
            print(f'[JARVIS] Response: "{spoken[:80]}" (model={model}, {latency}ms)')

            clients = getattr(self, '_active_clients', {})
            is_browser = clients.get("browser", False)
            is_desktop = clients.get("desktop", False)

            # Broadcast message to all clients
            # If server will handle TTS via ffplay, don't send 'spoken' to frontend
            # (otherwise both browser Audio API AND ffplay play = double voice)
            server_will_speak = (is_desktop or not is_browser) and spoken and len(spoken) > 1
            await self._broadcast({
                "type": "message", "role": "jarvis",
                "content": response,
                "spoken": "" if server_will_speak else spoken,
                "model": model, "latency_ms": latency,
                "voice_style": "default",
                "server_tts": server_will_speak,  # tell frontend server handles TTS
            })

            # TTS: desktop uses server-side ffplay, browser uses Audio API
            if server_will_speak:
                # Mute mic BEFORE speaking to prevent echo
                if hasattr(self, '_server_listener'):
                    self._server_listener.jarvis_speaking = True
                await self._broadcast({"type": "status", "status": "speaking"})
                print(f"[JARVIS] Speaking via ffplay: \"{spoken[:60]}\"")
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
                # Post-speech cooldown — let residual room audio decay before listening
                await asyncio.sleep(0.5)
                self._server_listener.jarvis_speaking = False

    async def _broadcast(self, data: dict):
        """Send a JSON message to all connected WebSocket clients. Prunes dead ones."""
        dead = []
        for ws in list(self.clients):
            try:
                if not ws.closed:
                    await ws.send_json(data)
                else:
                    dead.append(ws)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    async def _speak_short(self, text: str):
        """Quick TTS for short status phrases like 'Let me check'."""
        try:
            import tempfile
            # Mute mic during narration to prevent echo
            if hasattr(self, '_server_listener'):
                self._server_listener.jarvis_speaking = True
            communicate = edge_tts.Communicate(text, TTS_VOICE)
            audio_data = io.BytesIO()
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_data.write(chunk["data"])
            audio_bytes = audio_data.getvalue()
            if not audio_bytes:
                return
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                f.write(audio_bytes)
                tmp_path = f.name
            try:
                proc = await asyncio.create_subprocess_exec(
                    "ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", tmp_path,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                self._current_ffplay = proc  # track so it can be cancelled
                await asyncio.wait_for(proc.wait(), timeout=15)
            except asyncio.TimeoutError:
                proc.kill()
            except asyncio.CancelledError:
                try: proc.kill()
                except: pass
                raise
            finally:
                self._current_ffplay = None
                try: os.unlink(tmp_path)
                except: pass
            print(f"[JARVIS] Narrating: \"{text}\"")
        except asyncio.CancelledError:
            pass  # cancelled by final response — that's fine
        except Exception as e:
            print(f"[JARVIS] Short TTS error: {e}")
        finally:
            # Post-speech cooldown — let residual room audio decay
            await asyncio.sleep(0.3)
            if hasattr(self, '_server_listener'):
                self._server_listener.jarvis_speaking = False

    async def _speak_system(self, text: str):
        """Generate TTS and play with timeout protection."""
        import tempfile

        try:
            voice = TTS_VOICE
            communicate = edge_tts.Communicate(text, voice)

            # Stream TTS audio with timeout
            audio_data = io.BytesIO()
            async def _stream():
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        audio_data.write(chunk["data"])

            await asyncio.wait_for(_stream(), timeout=30)

            audio_bytes = audio_data.getvalue()
            if not audio_bytes:
                return

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
                # ffplay timeout — kill if stuck (broken audio device)
                try:
                    await asyncio.wait_for(proc.wait(), timeout=60)
                except asyncio.TimeoutError:
                    proc.kill()
                    print("[JARVIS] TTS playback timed out, killed ffplay")
            except FileNotFoundError:
                pass
            finally:
                self._current_ffplay = None
                try: os.unlink(tmp_path)
                except: pass

        except asyncio.TimeoutError:
            print("[JARVIS] Edge TTS streaming timed out")
        except Exception as e:
            print(f'[JARVIS] TTS error: {e}')
            self._current_ffplay = None

    # ── Remote Session API ──────────────────────────────────────────

    def _check_remote_auth(self, request: web.Request) -> bool:
        """Validate the remote auth token from the request.

        Returns True if auth is valid or no auth is configured.
        """
        if not self._remote_auth_token:
            return True  # No auth configured
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            return token == self._remote_auth_token
        # Also accept token as query param for WebSocket upgrades
        token = request.query.get("token", "")
        return token == self._remote_auth_token

    async def remote_connect_handler(self, request: web.Request) -> web.Response:
        """POST /api/remote/connect — create a remote session.

        Request body:
            { "cwd": "/path/to/work", "session_id": "optional-id" }

        Returns:
            { "session_id": "...", "ws_url": "ws://host:port/ws/remote?session=..." }
        """
        if not self._check_remote_auth(request):
            return web.json_response({"error": "Authentication required"}, status=401)

        try:
            data = await request.json()
        except Exception:
            data = {}

        cwd = data.get("cwd", os.getcwd() if hasattr(os, "getcwd") else "/")
        requested_id = data.get("session_id")

        session = await self.remote_manager.create_session(
            config={"cwd": cwd, "created_by": request.remote},
            session_id=requested_id,
        )

        # Build WebSocket URL
        scheme = "wss" if request.secure else "ws"
        host = request.host
        ws_url = f"{scheme}://{host}/ws/remote?session={session.session_id}"

        logger.info("[remote] New session %s from %s", session.session_id, request.remote)

        return web.json_response({
            "session_id": session.session_id,
            "ws_url": ws_url,
            "status": "connected",
        })

    async def remote_disconnect_handler(self, request: web.Request) -> web.Response:
        """POST /api/remote/disconnect — end a remote session.

        Request body:
            { "session_id": "..." }
        """
        if not self._check_remote_auth(request):
            return web.json_response({"error": "Authentication required"}, status=401)

        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid request body"}, status=400)

        session_id = data.get("session_id", "")
        if not session_id:
            return web.json_response({"error": "session_id required"}, status=400)

        stopped = await self.remote_manager.stop_session(session_id)
        if stopped:
            return web.json_response({"status": "disconnected", "session_id": session_id})
        return web.json_response({"error": "Session not found"}, status=404)

    async def remote_status_handler(self, request: web.Request) -> web.Response:
        """GET /api/remote/status — check remote session status.

        Returns bridge status and all active sessions.
        """
        if not self._check_remote_auth(request):
            return web.json_response({"error": "Authentication required"}, status=401)

        sessions = self.remote_manager.list_session_info()
        bridge_enabled = is_bridge_enabled()
        return web.json_response({
            "bridge_enabled": bridge_enabled,
            "connected": self.remote_manager.is_connected(),
            "active_sessions": self.remote_manager.active_count,
            "max_sessions": self.remote_manager._max_sessions,
            "sessions": sessions,
        })

    async def remote_websocket_handler(self, request: web.Request) -> web.WebSocketResponse:
        """WS /ws/remote — WebSocket endpoint for remote clients.

        Remote clients connect here to send queries and receive responses,
        mirroring the local /ws endpoint but with session tracking and auth.

        Query params:
            session: session_id (from /api/remote/connect)
            token: auth token (alternative to Authorization header)
        """
        if not self._check_remote_auth(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            await ws.send_json({"type": "error", "error": "Authentication required"})
            await ws.close()
            return ws

        session_id = request.query.get("session", "")

        ws = web.WebSocketResponse(heartbeat=30.0)
        await ws.prepare(request)

        # If no session exists yet, create one on-the-fly
        session = self.remote_manager.get_session(session_id) if session_id else None
        if session is None:
            session = await self.remote_manager.create_session(
                config={"cwd": os.getcwd(), "created_by": request.remote, "auto": True},
                ws=ws,
                session_id=session_id or None,
            )
        else:
            session.ws = ws
            session.status = "connected"

        # Also track as a regular client for broadcasts
        self.clients.add(ws)
        peer = request.remote
        logger.info("[remote] WebSocket client connected: %s session=%s", peer, session.session_id)

        # Send welcome message
        await ws.send_json({
            "type": "remote_connected",
            "session_id": session.session_id,
            "message": "JARVIS remote session active.",
        })

        # UUID dedup sets for bridge message handling
        recent_posted = BoundedUUIDSet(500)
        recent_inbound = BoundedUUIDSet(500)

        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.BINARY:
                    await self._handle_audio(ws, msg.data)
                elif msg.type == web.WSMsgType.TEXT:
                    session.touch()
                    try:
                        data = json.loads(msg.data)
                    except json.JSONDecodeError:
                        continue

                    msg_type = data.get("type", "query")

                    # Handle bridge protocol messages
                    if msg_type in ("user", "assistant", "system", "control_response", "control_request"):
                        handle_ingress_message(
                            msg.data,
                            recent_posted,
                            recent_inbound,
                            on_inbound_message=lambda parsed: asyncio.ensure_future(
                                self._handle_remote_inbound(ws, session, parsed)
                            ),
                            on_permission_response=lambda parsed: (
                                self.remote_permission_bridge.handle_permission_response(
                                    parsed.get("response", {}).get("request_id", ""),
                                    parsed.get("response", {}),
                                )
                            ),
                        )
                        continue

                    # Standard JARVIS message types (same as local /ws)
                    if msg_type == "query":
                        await self._handle_query(ws, data)
                    elif msg_type == "stats":
                        await ws.send_json({"type": "stats", "stats": self.brain.brain_stats()})
                    elif msg_type == "learn":
                        r = self.brain.learn(data.get("text", ""))
                        await ws.send_json({"type": "message", "role": "jarvis", "content": r})
                    elif msg_type == "recall":
                        m = self.brain.remember(data.get("text", ""))
                        await ws.send_json({"type": "memories", "memories": m})
                    elif msg_type == "list_providers":
                        providers = self.brain.reasoner.providers.list_providers()
                        await ws.send_json({"type": "providers", "providers": providers})
                    elif msg_type == "ping":
                        await ws.send_json({"type": "pong", "session_id": session.session_id})

        except asyncio.CancelledError:
            pass
        finally:
            self.clients.discard(ws)
            session.status = "disconnected"
            if not ws.closed:
                await ws.close()
            logger.info("[remote] WebSocket client disconnected: %s session=%s", peer, session.session_id)

        return ws

    # ── Bridge protocol endpoints (/v1/environments/*) ─────────────────
    # These let bridgeApi.py (standalone bridge mode) work against
    # JARVIS's own server — register, poll, ack, heartbeat, deregister.

    async def bridge_register_handler(self, request: web.Request) -> web.Response:
        """POST /v1/environments/bridge — register a bridge environment."""
        if not self._check_remote_auth(request):
            return web.json_response(
                {"error": {"type": "auth_error", "message": "Authentication required"}},
                status=401,
            )
        try:
            data = await request.json()
        except Exception:
            data = {}

        import uuid as _uuid

        env_id = data.get("environment_id") or str(_uuid.uuid4())
        env_secret = secrets.token_urlsafe(32)

        # Store environment metadata on the remote manager
        if not hasattr(self.remote_manager, "_bridge_envs"):
            self.remote_manager._bridge_envs = {}
        self.remote_manager._bridge_envs[env_id] = {
            "secret": env_secret,
            "machine_name": data.get("machine_name", ""),
            "directory": data.get("directory", ""),
            "branch": data.get("branch", ""),
            "max_sessions": data.get("max_sessions", 5),
            "work_queue": asyncio.Queue(),
        }
        self.remote_manager.set_connected(True)

        logger.info("[bridge] Environment registered: %s (%s)", env_id, data.get("machine_name", ""))
        return web.json_response({
            "environment_id": env_id,
            "environment_secret": env_secret,
        })

    async def bridge_poll_handler(self, request: web.Request) -> web.Response:
        """GET /v1/environments/{env_id}/work/poll — poll for pending work."""
        env_id = request.match_info["env_id"]
        if not self._check_remote_auth(request):
            return web.json_response(
                {"error": {"type": "auth_error", "message": "Authentication required"}},
                status=401,
            )

        envs = getattr(self.remote_manager, "_bridge_envs", {})
        env = envs.get(env_id)
        if not env:
            return web.json_response(
                {"error": {"type": "not_found", "message": "Environment not found"}},
                status=404,
            )

        # Non-blocking check for work in the queue
        queue: asyncio.Queue = env["work_queue"]
        try:
            work = queue.get_nowait()
            return web.json_response(work)
        except asyncio.QueueEmpty:
            return web.json_response(None, status=204)

    async def bridge_ack_handler(self, request: web.Request) -> web.Response:
        """POST /v1/environments/{env_id}/work/{work_id}/ack — acknowledge work."""
        if not self._check_remote_auth(request):
            return web.json_response(
                {"error": {"type": "auth_error", "message": "Authentication required"}},
                status=401,
            )
        return web.json_response({"status": "acknowledged"})

    async def bridge_heartbeat_handler(self, request: web.Request) -> web.Response:
        """POST /v1/environments/{env_id}/work/{work_id}/heartbeat — session heartbeat."""
        if not self._check_remote_auth(request):
            return web.json_response(
                {"error": {"type": "auth_error", "message": "Authentication required"}},
                status=401,
            )
        return web.json_response({"status": "alive", "actions": []})

    async def bridge_stop_work_handler(self, request: web.Request) -> web.Response:
        """POST /v1/environments/{env_id}/work/{work_id}/stop — stop work."""
        if not self._check_remote_auth(request):
            return web.json_response(
                {"error": {"type": "auth_error", "message": "Authentication required"}},
                status=401,
            )
        work_id = request.match_info["work_id"]
        stopped = await self.remote_manager.stop_session(work_id)
        return web.json_response({"status": "stopped" if stopped else "not_found"})

    async def bridge_deregister_handler(self, request: web.Request) -> web.Response:
        """DELETE /v1/environments/bridge/{env_id} — deregister environment."""
        if not self._check_remote_auth(request):
            return web.json_response(
                {"error": {"type": "auth_error", "message": "Authentication required"}},
                status=401,
            )
        env_id = request.match_info["env_id"]
        envs = getattr(self.remote_manager, "_bridge_envs", {})
        envs.pop(env_id, None)
        logger.info("[bridge] Environment deregistered: %s", env_id)
        return web.json_response({"status": "deregistered"})

    async def bridge_session_events_handler(self, request: web.Request) -> web.Response:
        """POST /v1/sessions/{session_id}/events — send events to a session."""
        if not self._check_remote_auth(request):
            return web.json_response(
                {"error": {"type": "auth_error", "message": "Authentication required"}},
                status=401,
            )
        session_id = request.match_info["session_id"]
        session = self.remote_manager.get_session(session_id)
        if session and session.is_alive:
            try:
                data = await request.json()
                events = data.get("events", [])
                for event in events:
                    await session.ws.send_json(event)
            except Exception:
                pass
        return web.json_response({"status": "sent"})

    async def bridge_session_archive_handler(self, request: web.Request) -> web.Response:
        """POST /v1/sessions/{session_id}/archive — archive a session."""
        if not self._check_remote_auth(request):
            return web.json_response(
                {"error": {"type": "auth_error", "message": "Authentication required"}},
                status=401,
            )
        session_id = request.match_info["session_id"]
        await self.remote_manager.stop_session(session_id)
        return web.json_response({"status": "archived"})

    async def bridge_reconnect_handler(self, request: web.Request) -> web.Response:
        """POST /v1/environments/{env_id}/bridge/reconnect — reconnect a session."""
        if not self._check_remote_auth(request):
            return web.json_response(
                {"error": {"type": "auth_error", "message": "Authentication required"}},
                status=401,
            )
        try:
            data = await request.json()
        except Exception:
            data = {}
        session_id = data.get("session_id", "")
        session = self.remote_manager.get_session(session_id)
        if session:
            session.status = "connected"
            return web.json_response({"status": "reconnected"})
        return web.json_response(
            {"error": {"type": "not_found", "message": "Session not found"}},
            status=404,
        )

    async def _handle_remote_inbound(
        self, ws: web.WebSocketResponse, session, parsed: dict
    ) -> None:
        """Handle an inbound user message from a remote bridge client."""
        content = ""
        message = parsed.get("message", {})
        if isinstance(message, dict):
            content = message.get("content", "")
        if isinstance(content, list):
            # Extract text from content blocks
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    content = block.get("text", "")
                    break
        if isinstance(content, str) and content.strip():
            await self._handle_query(ws, {"text": content.strip()})

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

        from src.agent.system_agents import SystemAgent

        actions = {
            "shutdown":  SystemAgent.shutdown,
            "reboot":    SystemAgent.reboot,
            "sleep":     SystemAgent.hybrid_sleep,
            "hibernate": SystemAgent.hibernate,
            "suspend":   SystemAgent.suspend,
            "lock":      SystemAgent.lock,
        }

        if action not in actions:
            return web.json_response({"error": f"Unknown action: {action}"}, status=400)

        fn = actions[action]
        await self._broadcast({"type": "power", "action": action})
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
            # Reload brain modules
            import sys
            brain_modules = [name for name in sys.modules if name.startswith("brain.")]
            for name in brain_modules:
                mod = sys.modules[name]
                if hasattr(mod, '__file__') and mod.__file__:
                    importlib.reload(mod)
                    reloaded.append(name.split(".")[-1])

            # Rebuild brain
            from src.brain import Brain
            self.brain = Brain(quiet=True)
            reloaded.append("brain_restarted")

            print(f"[JARVIS] Hot reload: {', '.join(reloaded)}")
            return web.json_response({"status": "reloaded", "modules": reloaded})
        except Exception as e:
            print(f"[JARVIS] Reload failed: {e}")
            return web.json_response({"status": "error", "error": str(e)}, status=500)

    async def run(self):
        # Write PID file for reliable shutdown
        with open("/tmp/jarvis-server.pid", "w") as f:
            f.write(str(os.getpid()))

        app = web.Application(client_max_size=16 * 1024 * 1024)  # 16MB max request
        app.router.add_get("/ws", self.websocket_handler)
        app.router.add_get("/tts", self.tts_handler)
        app.router.add_get("/api/tts", self.tts_handler)
        app.router.add_get("/tts/chunks", self.tts_chunks_handler)
        app.router.add_post("/api/transcribe", self._transcribe_handler)
        app.router.add_get("/jarvis_package.tar.gz", self.package_handler)
        app.router.add_get("/dropper.sh", self.dropper_handler)
        # Readiness check — returns 200 only when brain is fully initialized
        async def _ready_check(request):
            if self.brain is not None:
                return web.json_response({"ready": True})
            return web.json_response({"ready": False}, status=503)
        app.router.add_get("/api/ready", _ready_check)

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

        # Provider setup API — add/test providers, check Ollama
        async def _provider_add(request):
            """Add or update a provider. For Ollama, just switch the model."""
            data = await request.json()
            name = data.get("name", "")
            ptype = data.get("type", "openai")
            api_key = data.get("api_key", "")
            base_url = data.get("base_url", "")
            model = data.get("model", "")
            skip_test = data.get("skip_test", False)
            if not name or not api_key:
                return web.json_response({"ok": False, "error": "Missing name or api_key"}, status=400)

            # For Ollama local models — skip the slow test, just verify model exists
            is_local = "localhost" in base_url or "127.0.0.1" in base_url
            if not skip_test:
                try:
                    if is_local:
                        # Just check model exists via Ollama API (fast)
                        import urllib.request as _ur
                        r = _ur.urlopen(f"{base_url.replace('/v1','')}/api/tags", timeout=3)
                        models_data = json.loads(r.read())
                        available = [m["name"] for m in models_data.get("models", [])]
                        if model not in available and not any(model in m for m in available):
                            return web.json_response({"ok": False, "error": f"Model '{model}' not found. Available: {', '.join(available[:5])}"})
                    elif ptype == "anthropic":
                        import anthropic
                        client = anthropic.Anthropic(api_key=api_key)
                        client.messages.create(model=model, max_tokens=10,
                            messages=[{"role": "user", "content": "hi"}])
                    else:
                        import openai
                        client = openai.OpenAI(api_key=api_key, base_url=base_url)
                        client.chat.completions.create(model=model, max_tokens=10,
                            messages=[{"role": "user", "content": "hi"}], timeout=10)
                except Exception as e:
                    return web.json_response({"ok": False, "error": str(e)[:200]})

            # Save to providers.json — update existing or add new
            providers_path = os.path.expanduser("~/.jarvis/providers.json")
            try:
                with open(providers_path) as f:
                    providers = json.load(f)
            except Exception:
                providers = {}

            if name in providers:
                # Update existing — switch the model and make it primary
                providers[name]["model"] = model
                if model not in providers[name].get("models", []):
                    providers[name]["models"].insert(0, model)
                # Make this provider highest priority (0) and bump others down
                providers[name]["priority"] = 0
                for k, v in providers.items():
                    if k != name:
                        v["priority"] = max(1, v.get("priority", 1))
            else:
                providers[name] = {
                    "name": name, "type": ptype, "api_key": api_key,
                    "base_url": base_url, "model": model,
                    "models": [model], "priority": len(providers), "enabled": True,
                }
            with open(providers_path, "w") as f:
                json.dump(providers, f, indent=2)

            # Reload providers in the brain
            if self.brain and hasattr(self.brain, 'reasoner'):
                self.brain.reasoner.providers = __import__(
                    'src.reasoning.providers', fromlist=['ProviderRegistry']
                ).ProviderRegistry()

            print(f"[JARVIS] Provider switched: {name} → {model}")
            return web.json_response({"ok": True, "provider": name, "model": model})

        async def _provider_current(request):
            """Get the current active provider and model."""
            try:
                providers_path = os.path.expanduser("~/.jarvis/providers.json")
                with open(providers_path) as f:
                    providers = json.load(f)
                # Find highest priority enabled provider
                active = sorted(
                    [(k, v) for k, v in providers.items() if v.get("enabled")],
                    key=lambda x: x[1].get("priority", 99)
                )
                if active:
                    name, p = active[0]
                    return web.json_response({
                        "provider": name,
                        "model": p.get("model", ""),
                        "type": p.get("type", ""),
                        "all_providers": [{
                            "name": k, "model": v.get("model"), "priority": v.get("priority"),
                            "enabled": v.get("enabled"), "type": v.get("type"),
                        } for k, v in providers.items()],
                    })
            except Exception:
                pass
            return web.json_response({"provider": "none", "model": "none"})

        async def _ollama_status(request):
            """Check if Ollama is running and list models."""
            try:
                import urllib.request
                r = urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3)
                data = json.loads(r.read())
                models = [m["name"] for m in data.get("models", [])]
                return web.json_response({"online": True, "models": models})
            except Exception:
                return web.json_response({"online": False, "models": []})

        async def _ollama_pull(request):
            """Pull a model via Ollama."""
            data = await request.json()
            model = data.get("model", "")
            if not model:
                return web.json_response({"ok": False, "error": "No model specified"}, status=400)
            import subprocess
            proc = subprocess.Popen(
                ["ollama", "pull", model],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            stdout, stderr = proc.communicate(timeout=300)
            if proc.returncode == 0:
                return web.json_response({"ok": True, "model": model})
            return web.json_response({"ok": False, "error": stderr.decode()[:200]})

        async def _model_search(request):
            """Search for downloadable models from Ollama library and HuggingFace."""
            query = request.query.get("q", "").strip().lower()
            if not query or len(query) < 2:
                return web.json_response({"models": []})

            # Hardware info for compatibility check
            try:
                from src.hardware import detect_hardware
                hw = detect_hardware()
                avail_ram = hw.available_ram_gb
                has_gpu = hw.has_nvidia
                vram_gb = 0
                if has_gpu:
                    import subprocess as _sp_vram
                    r = _sp_vram.run(['nvidia-smi', '--query-gpu=memory.free', '--format=csv,noheader,nounits'],
                        capture_output=True, text=True, timeout=3)
                    if r.returncode == 0:
                        vram_gb = int(r.stdout.strip()) / 1024
            except Exception:
                avail_ram, has_gpu, vram_gb = 64, False, 0

            def _check_compat(size_str, model_name=""):
                """Check if model fits current hardware. Returns (fits, reason)."""
                # Parse size from string like "43GB", "4.4GB", "2.0GB"
                size_gb = 0
                if size_str:
                    import re as _re_sz
                    m = _re_sz.search(r'([\d.]+)\s*[Gg][Bb]', size_str)
                    if m:
                        size_gb = float(m.group(1))
                # Estimate from model name if no explicit size
                if not size_gb:
                    name_lower = (model_name or "").lower()
                    if "70b" in name_lower or "72b" in name_lower:
                        size_gb = 42
                    elif "32b" in name_lower or "34b" in name_lower:
                        size_gb = 20
                    elif "13b" in name_lower or "14b" in name_lower or "16b" in name_lower:
                        size_gb = 9
                    elif "7b" in name_lower or "8b" in name_lower:
                        size_gb = 5
                    elif "3b" in name_lower:
                        size_gb = 2
                    elif "1b" in name_lower:
                        size_gb = 1

                if size_gb == 0:
                    return "unknown", "Size unknown — try it"
                if size_gb <= avail_ram * 0.8:
                    if size_gb <= 6 and has_gpu:
                        return "perfect", f"Fits GPU ({size_gb:.0f}GB)"
                    return "good", f"Fits RAM ({size_gb:.0f}GB / {avail_ram:.0f}GB avail)"
                elif size_gb <= avail_ram:
                    return "tight", f"Tight fit ({size_gb:.0f}GB / {avail_ram:.0f}GB avail)"
                else:
                    return "too_large", f"Too large ({size_gb:.0f}GB > {avail_ram:.0f}GB avail)"

            results = []

            # Ollama library — curated models that work well
            ollama_models = {
                "llama3": [
                    {"id": "llama3.3", "name": "Llama 3.3 70B", "size": "43GB", "source": "ollama"},
                    {"id": "llama3.2:3b", "name": "Llama 3.2 3B", "size": "2.0GB", "source": "ollama"},
                    {"id": "llama3.2:1b", "name": "Llama 3.2 1B", "size": "1.3GB", "source": "ollama"},
                    {"id": "llama3.1:8b", "name": "Llama 3.1 8B", "size": "4.7GB", "source": "ollama"},
                ],
                "qwen": [
                    {"id": "qwen2.5:72b", "name": "Qwen 2.5 72B", "size": "47GB", "source": "ollama"},
                    {"id": "qwen2.5:32b", "name": "Qwen 2.5 32B", "size": "20GB", "source": "ollama"},
                    {"id": "qwen2.5:7b", "name": "Qwen 2.5 7B", "size": "4.7GB", "source": "ollama"},
                    {"id": "qwen2.5:3b", "name": "Qwen 2.5 3B", "size": "1.9GB", "source": "ollama"},
                    {"id": "qwen3:8b", "name": "Qwen 3 8B", "size": "5.2GB", "source": "ollama"},
                ],
                "mistral": [
                    {"id": "mistral", "name": "Mistral 7B", "size": "4.1GB", "source": "ollama"},
                    {"id": "mistral-small", "name": "Mistral Small 24B", "size": "14GB", "source": "ollama"},
                ],
                "phi": [
                    {"id": "phi4", "name": "Phi-4 14B", "size": "9.1GB", "source": "ollama"},
                    {"id": "phi3:mini", "name": "Phi-3 Mini 3.8B", "size": "2.3GB", "source": "ollama"},
                ],
                "gemma": [
                    {"id": "gemma2:27b", "name": "Gemma 2 27B", "size": "16GB", "source": "ollama"},
                    {"id": "gemma2:9b", "name": "Gemma 2 9B", "size": "5.5GB", "source": "ollama"},
                    {"id": "gemma2:2b", "name": "Gemma 2 2B", "size": "1.6GB", "source": "ollama"},
                ],
                "deepseek": [
                    {"id": "deepseek-r1:8b", "name": "DeepSeek R1 8B", "size": "4.9GB", "source": "ollama"},
                    {"id": "deepseek-r1:14b", "name": "DeepSeek R1 14B", "size": "9.0GB", "source": "ollama"},
                    {"id": "deepseek-coder-v2:16b", "name": "DeepSeek Coder V2 16B", "size": "8.9GB", "source": "ollama"},
                ],
                "codellama": [
                    {"id": "codellama:13b", "name": "Code Llama 13B", "size": "7.4GB", "source": "ollama"},
                    {"id": "codellama:7b", "name": "Code Llama 7B", "size": "3.8GB", "source": "ollama"},
                ],
                "starcoder": [
                    {"id": "starcoder2:7b", "name": "StarCoder2 7B", "size": "4.0GB", "source": "ollama"},
                ],
                "moondream": [
                    {"id": "moondream", "name": "Moondream 2 (Vision)", "size": "1.7GB", "source": "ollama"},
                ],
                "llava": [
                    {"id": "llava:7b", "name": "LLaVA 7B (Vision)", "size": "4.7GB", "source": "ollama"},
                ],
            }

            for key, models in ollama_models.items():
                if query in key or key in query:
                    results.extend(models)

            # If no exact match, fuzzy search all
            if not results:
                for key, models in ollama_models.items():
                    for m in models:
                        if query in m["id"].lower() or query in m["name"].lower():
                            results.append(m)

            # HuggingFace lookup and search
            import urllib.request, urllib.error

            # Direct lookup if query looks like org/model
            if "/" in query:
                try:
                    hf_url = f"https://huggingface.co/api/models/{query}"
                    req = urllib.request.Request(hf_url, headers={"User-Agent": "JARVIS/3.0"})
                    resp = urllib.request.urlopen(req, timeout=5)
                    m = json.loads(resp.read())
                    tags = m.get("tags", [])
                    has_gguf = any("gguf" in str(t).lower() for t in tags)
                    results.append({
                        "id": m.get("modelId", query),
                        "name": m.get("modelId", "").split("/")[-1],
                        "size": "",
                        "source": "huggingface-gguf" if has_gguf else "huggingface",
                        "pipeline": m.get("pipeline_tag", ""),
                        "downloads": m.get("downloads", 0),
                    })
                except urllib.error.HTTPError:
                    pass  # model not found
                except Exception as e:
                    print(f"[JARVIS] HF lookup error: {e}")

            # Search HuggingFace — GGUF first, then all
            search_q = query.split("/")[-1] if "/" in query else query
            for hf_filter in ["gguf", ""]:
                if len(results) >= 10:
                    break
                try:
                    filter_param = f"&filter={hf_filter}" if hf_filter else ""
                    hf_url = f"https://huggingface.co/api/models?search={search_q}{filter_param}&sort=downloads&limit=5"
                    req = urllib.request.Request(hf_url, headers={"User-Agent": "JARVIS/3.0"})
                    resp = urllib.request.urlopen(req, timeout=5)
                    hf_data = json.loads(resp.read())
                    for m in hf_data:
                        mid = m.get("modelId", "")
                        if any(r["id"] == mid for r in results):
                            continue
                        tags = m.get("tags", [])
                        has_gguf = any("gguf" in str(t).lower() for t in tags)
                        results.append({
                            "id": mid,
                            "name": mid.split("/")[-1],
                            "size": "",
                            "source": "huggingface-gguf" if has_gguf else "huggingface",
                            "pipeline": m.get("pipeline_tag", ""),
                            "downloads": m.get("downloads", 0),
                        })
                except Exception:
                    pass

            # Add hardware compatibility to each result
            for r in results:
                compat, reason = _check_compat(r.get("size", ""), r.get("name", r.get("id", "")))
                r["compat"] = compat
                r["compat_reason"] = reason

            return web.json_response({
                "models": results[:15],
                "hardware": {
                    "ram_gb": round(avail_ram),
                    "vram_gb": round(vram_gb, 1),
                    "gpu": has_gpu,
                },
            })

        async def _model_download(request):
            """Download a model — from Ollama or HuggingFace."""
            data = await request.json()
            model = data.get("model", "")
            source = data.get("source", "ollama")

            if not model:
                return web.json_response({"ok": False, "error": "No model specified"}, status=400)

            if source == "ollama":
                # Pull via Ollama
                import subprocess
                try:
                    proc = subprocess.Popen(
                        ["ollama", "pull", model],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    )
                    stdout, stderr = proc.communicate(timeout=600)
                    if proc.returncode == 0:
                        return web.json_response({"ok": True, "model": model})
                    return web.json_response({"ok": False, "error": stderr.decode()[:200]})
                except subprocess.TimeoutExpired:
                    proc.kill()
                    return web.json_response({"ok": False, "error": "Download timed out (10 min limit)"})
                except FileNotFoundError:
                    return web.json_response({"ok": False, "error": "Ollama not installed. Run: curl -fsSL https://ollama.ai/install.sh | sh"})
            elif source == "huggingface":
                # For HuggingFace GGUF, try ollama pull with full path
                import subprocess
                try:
                    proc = subprocess.Popen(
                        ["ollama", "pull", f"hf.co/{model}"],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    )
                    stdout, stderr = proc.communicate(timeout=600)
                    if proc.returncode == 0:
                        return web.json_response({"ok": True, "model": model})
                    return web.json_response({"ok": False, "error": stderr.decode()[:200]})
                except Exception as e:
                    return web.json_response({"ok": False, "error": str(e)[:200]})

            return web.json_response({"ok": False, "error": f"Unknown source: {source}"})

        app.router.add_post("/api/provider/add", _provider_add)
        app.router.add_get("/api/provider/current", _provider_current)
        app.router.add_get("/api/ollama/status", _ollama_status)
        app.router.add_post("/api/ollama/pull", _ollama_pull)
        app.router.add_get("/api/models/search", _model_search)
        async def _model_upload(request):
            """Upload a local GGUF model file and import into Ollama."""
            # Size limit: 50GB (large models can be big)
            MAX_UPLOAD_SIZE = 50 * 1024 * 1024 * 1024
            ALLOWED_EXTENSIONS = {'gguf', 'ggml', 'bin', 'safetensors', 'pt', 'onnx'}

            reader = await request.multipart()
            field = await reader.next()
            if not field or field.name != 'model':
                return web.json_response({"ok": False, "error": "No model file"}, status=400)

            filename = field.filename or "uploaded_model.gguf"
            ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else 'gguf'

            # Validate extension
            if ext not in ALLOWED_EXTENSIONS:
                return web.json_response({
                    "ok": False,
                    "error": f"Invalid file type .{ext}. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
                }, status=400)

            # Sanitize model name (alphanumeric, hyphens, underscores only)
            model_name = re.sub(r'[^a-z0-9_-]', '-', filename.rsplit('.', 1)[0].lower())

            # Save uploaded file to secure temp directory
            import tempfile
            tmp_dir = tempfile.mkdtemp(prefix="jarvis-upload-")
            tmp_path = os.path.join(tmp_dir, f"model.{ext}")
            size = 0
            with open(tmp_path, 'wb') as tmp_f:
                while True:
                    chunk = await field.read_chunk(8192)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_UPLOAD_SIZE:
                        os.unlink(tmp_path)
                        os.rmdir(tmp_dir)
                        return web.json_response({
                            "ok": False, "error": f"File too large (max {MAX_UPLOAD_SIZE // (1024**3)}GB)",
                        }, status=413)
                    tmp_f.write(chunk)

            # Create a fake tmp object for compatibility with downstream code
            class _TmpCompat:
                name = tmp_path
            tmp = _TmpCompat()
            print(f"[JARVIS] Model uploaded: {filename} ({size/1024/1024:.0f} MB) → {tmp.name}")

            # Import into Ollama — GGUF/GGML import directly, others need conversion
            if ext not in ('gguf', 'ggml', 'bin'):
                # safetensors/pt/onnx — Ollama can't import these directly
                # Try llama.cpp convert if available, otherwise inform user
                try:
                    proc = subprocess.run(
                        ["python3", "-m", "llama_cpp.convert", tmp.name, "--outfile", f"{tmp.name}.gguf"],
                        capture_output=True, timeout=300,
                    )
                    if proc.returncode == 0:
                        os.unlink(tmp.name)
                        tmp_name_orig = tmp.name
                        tmp.name = tmp_name_orig + '.gguf'
                    else:
                        os.unlink(tmp.name)
                        return web.json_response({
                            "ok": False,
                            "error": f".{ext} format needs conversion. Install llama-cpp-python or convert to .gguf first.",
                        })
                except Exception:
                    os.unlink(tmp.name)
                    return web.json_response({
                        "ok": False,
                        "error": f".{ext} format not directly supported. Convert to .gguf first (use llama.cpp or HuggingFace).",
                    })

            modelfile = f"FROM {tmp.name}\n"
            modelfile_path = f"/tmp/jarvis_modelfile_{model_name}"
            with open(modelfile_path, 'w') as f:
                f.write(modelfile)

            try:
                proc = subprocess.Popen(
                    ["ollama", "create", model_name, "-f", modelfile_path],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                )
                stdout, stderr = proc.communicate(timeout=300)
                if proc.returncode == 0:
                    print(f"[JARVIS] Model imported as '{model_name}'")
                    # Clean up
                    os.unlink(tmp.name)
                    os.unlink(modelfile_path)
                    return web.json_response({"ok": True, "model": model_name, "size_mb": size // (1024*1024)})
                else:
                    return web.json_response({"ok": False, "error": stderr.decode()[:200]})
            except subprocess.TimeoutExpired:
                proc.kill()
                return web.json_response({"ok": False, "error": "Import timed out (5 min limit)"})
            except FileNotFoundError:
                return web.json_response({"ok": False, "error": "Ollama not installed"})

        app.router.add_post("/api/models/download", _model_download)
        app.router.add_post("/api/models/upload", _model_upload)

        # Full restart — kills the process; systemd/start script relaunches
        async def _restart_handler(request):
            await self._broadcast({
                "type": "message", "role": "jarvis",
                "content": "Restarting...", "spoken": "",
            })
            print("[JARVIS] Restart requested via API — exiting for relaunch")
            await asyncio.sleep(1)
            import subprocess as _sp_k, signal as _sig
            _sp_k.run(["pkill", "-f", "src.desktop.app"], capture_output=True)
            os.kill(os.getpid(), _sig.SIGTERM)
        app.router.add_post("/api/restart", _restart_handler)
        # Remote session API — makes JARVIS cloud-capable
        app.router.add_post("/api/remote/connect", self.remote_connect_handler)
        app.router.add_post("/api/remote/disconnect", self.remote_disconnect_handler)
        app.router.add_get("/api/remote/status", self.remote_status_handler)
        app.router.add_get("/ws/remote", self.remote_websocket_handler)
        # Bridge protocol API — standalone bridge mode (bridgeApi.py compatible)
        app.router.add_post("/v1/environments/bridge", self.bridge_register_handler)
        app.router.add_get("/v1/environments/{env_id}/work/poll", self.bridge_poll_handler)
        app.router.add_post("/v1/environments/{env_id}/work/{work_id}/ack", self.bridge_ack_handler)
        app.router.add_post("/v1/environments/{env_id}/work/{work_id}/heartbeat", self.bridge_heartbeat_handler)
        app.router.add_post("/v1/environments/{env_id}/work/{work_id}/stop", self.bridge_stop_work_handler)
        app.router.add_delete("/v1/environments/bridge/{env_id}", self.bridge_deregister_handler)
        app.router.add_post("/v1/sessions/{session_id}/events", self.bridge_session_events_handler)
        app.router.add_post("/v1/sessions/{session_id}/archive", self.bridge_session_archive_handler)
        app.router.add_post("/v1/environments/{env_id}/bridge/reconnect", self.bridge_reconnect_handler)

        # ── Client coordination — only one reactor visible at a time ──
        active_clients = {"desktop": False, "browser": False}
        self._active_clients = active_clients  # Expose for _speak_system check

        async def client_register(request):
            """Register a client (desktop or browser).

            Seamless handoff: browser takes priority over desktop.
            When browser opens, desktop hides. When browser closes, desktop resumes.
            Only one UI renders the reactor at a time — no double display.
            """
            data = await request.json()
            client_type = data.get("type", "browser")  # "desktop" or "browser"
            active_clients[client_type] = True

            # Stop server mic only for browser (Chrome has SpeechRecognition)
            # Desktop WebKit can't reliably capture mic, so server mic stays on
            if client_type == "browser":
                if hasattr(self, '_server_mic_running') and self._server_mic_running:
                    self._server_mic_running = False
                    print(f"[JARVIS] browser connected — server mic stopped (browser handles voice)")
            else:
                print(f"[JARVIS] desktop connected — server mic stays on (WebKit can't capture mic)")

            # Browser always gets the reactor; desktop yields
            if client_type == "browser":
                show_reactor = True
            else:
                show_reactor = not active_clients.get("browser", False)

            return web.json_response({
                "show_reactor": show_reactor,
                "active_clients": active_clients,
            })

        async def client_unregister(request):
            """Client disconnecting."""
            data = await request.json()
            client_type = data.get("type", "browser")
            active_clients[client_type] = False

            # Restart server mic only if NO UI clients remain (headless mode)
            has_ui = active_clients.get("desktop") or active_clients.get("browser")
            if not has_ui and hasattr(self, '_server_mic_running') and not self._server_mic_running:
                print("[JARVIS] All UI clients disconnected — restarting server mic")
                import asyncio as _aio
                _aio.ensure_future(self._start_server_mic())

            return web.json_response({"ok": True, "active_clients": active_clients})

        async def client_status(request):
            """Check who's active."""
            return web.json_response(active_clients)

        async def client_handoff(request):
            """Switch JARVIS to a different UI surface.

            POST /api/client/handoff {target: "desktop" | "browser"}
            Broadcasts a handoff event so the other client closes/opens.
            """
            data = await request.json()
            target = data.get("target", "desktop")

            if target == "desktop":
                # Tell browser clients to close
                await self._broadcast({"type": "handoff", "target": "desktop"})
                active_clients["browser"] = False
            elif target == "browser":
                # Tell desktop to hide, open browser
                await self._broadcast({"type": "handoff", "target": "browser"})
                import subprocess as _sp
                env = {**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0.0")}
                _sp.Popen(["xdg-open", f"http://127.0.0.1:{PORT}/"],
                          start_new_session=True, stdout=_sp.DEVNULL,
                          stderr=_sp.DEVNULL, env=env)

            return web.json_response({"ok": True, "target": target, "active_clients": active_clients})

        app.router.add_post("/api/client/register", client_register)
        app.router.add_post("/api/client/unregister", client_unregister)
        app.router.add_get("/api/client/status", client_status)
        app.router.add_post("/api/client/handoff", client_handoff)

        # Chat API — used by React frontend
        async def think_handler(request):
            try:
                data = await request.json()
                query = data.get("query", data.get("text", ""))
                if not query:
                    return web.json_response({"error": "No query"}, status=400)
                # Use think_stream to get JARVIS personality (has casual chat detection)
                response = ""
                async for event in self.brain.think_stream(query):
                    if event.get("type") == "text":
                        response += event.get("content", "")
                    elif event.get("type") == "done":
                        break
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

        # ── Theme color API ──
        async def theme_get_handler(request):
            from src.desktop.colors import get_theme, get_colors, PRESETS
            theme = get_theme()
            primary, glow = get_colors()
            return web.json_response({
                "theme": theme,
                "primary": primary,
                "glow": glow,
                "presets": {k: {"primary": v[0], "glow": v[1], "label": v[2]}
                            for k, v in PRESETS.items()},
            })

        async def theme_set_handler(request):
            from src.desktop.colors import (
                PRESETS, set_theme, set_custom_color, get_colors, generate_icon,
            )
            data = await request.json()
            theme = data.get("theme")
            custom = data.get("custom")
            if custom:
                primary, glow = set_custom_color(custom, data.get("glow"))
            elif theme and theme in PRESETS:
                primary, glow = set_theme(theme)
            else:
                return web.json_response({"error": "Invalid theme"}, status=400)
            generate_icon(primary)
            # Push theme change to all connected clients in real-time
            await server._broadcast({
                "type": "theme_update",
                "primary": primary,
                "glow": glow,
                "theme": theme or "custom",
            })
            return web.json_response({"theme": theme or "custom", "primary": primary, "glow": glow})

        app.router.add_get("/api/theme", theme_get_handler)
        app.router.add_post("/api/theme", theme_set_handler)

        # ── Exclusive mode: desktop and browser cannot coexist ──
        # Desktop + CLI = OK.  Browser + CLI = OK.  Desktop + Browser = blocked.
        # Serve index.html for root and SPA fallback
        async def index_handler(request):
            # Read fresh on every request — picks up new builds without restart
            return web.Response(
                text=(STATIC_DIR / "index.html").read_text(),
                content_type="text/html",
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0",
                },
            )
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

        # Port is bound — now initialize the heavy Brain (MCP servers etc.)
        # This allows the desktop health check to pass while Brain loads.
        if self.brain is None:
            print("[JARVIS] Initializing Brain (MCP servers loading)...")
            # Tell connected clients init is starting
            await self._broadcast({
                "type": "status", "status": "initializing",
            })
            await self._broadcast({
                "type": "message", "role": "jarvis",
                "content": "Initializing systems...",
            })
            await asyncio.get_event_loop().run_in_executor(None, self._init_brain)
            await self.brain.start()
            print("[JARVIS] Brain ready.")

            # Launch desktop UI — kill old instance first to ensure fresh assets
            try:
                import subprocess as _sp_desktop
                # Kill any old desktop app so it picks up new JS/CSS
                # Use pgrep to find PIDs, then kill only those (avoids killing server)
                pgrep = _sp_desktop.run(
                    ["pgrep", "-f", "desktop.app import main"],
                    capture_output=True, text=True, timeout=5,
                )
                if pgrep.returncode == 0:
                    for pid in pgrep.stdout.strip().split('\n'):
                        if pid and pid != str(os.getpid()):
                            try:
                                os.kill(int(pid), 15)  # SIGTERM
                            except (ProcessLookupError, ValueError):
                                pass
                    await asyncio.sleep(1)
                _jarvis_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                env = {**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0.0")}
                _sp_desktop.Popen(
                    ["python3", "-c", "from src.desktop.app import main; main()"],
                    cwd=_jarvis_root, start_new_session=True,
                    stdout=_sp_desktop.DEVNULL, stderr=_sp_desktop.DEVNULL, env=env,
                )
                print("[JARVIS] Desktop UI launched.")
            except Exception as e:
                print(f"[JARVIS] Desktop UI launch skipped: {e}")

            # Tell connected clients JARVIS is ready — distinct event for UI indicator
            await self._broadcast({
                "type": "brain_ready",
                "tools": len(self.brain.mcp.get_tool_schemas()) + 40,
            })
            await self._broadcast({
                "type": "status", "status": "",
            })
            await self._broadcast({
                "type": "message", "role": "jarvis",
                "content": "All systems online. What do you need?",
            })

        # Start server-side mic capture as fallback
        # Pre-mute for a few seconds so mic doesn't pick up the startup TTS
        await self._start_server_mic()
        if hasattr(self, '_server_listener'):
            self._server_listener.jarvis_speaking = True
            self._last_response = "All systems online. What do you need?"

            async def _unmute_after_startup():
                await asyncio.sleep(6)  # Wait for startup TTS to finish
                if hasattr(self, '_server_listener'):
                    self._server_listener.jarvis_speaking = False
            asyncio.create_task(_unmute_after_startup())

        print(f"[JARVIS] Web shell:  http://localhost:{PORT}")
        print(f"[JARVIS] WebSocket:  ws://localhost:{PORT}/ws")
        print(f"[JARVIS] Remote WS:  ws://localhost:{PORT}/ws/remote")
        print(f"[JARVIS] Remote API: http://localhost:{PORT}/api/remote/status")
        print(f"[JARVIS] TTS:        http://localhost:{PORT}/tts?text=hello")

        # Auto-start bridge if configured
        if is_bridge_enabled():
            self.remote_manager.set_connected(True)
            remote_cfg = get_remote_config()
            print(f"[JARVIS] Remote bridge: ENABLED (max {self.remote_manager._max_sessions} sessions)")
            print(f"[JARVIS] Bridge API:   http://localhost:{PORT}/v1/environments/bridge")
            if remote_cfg.get("auth_token"):
                token_preview = remote_cfg["auth_token"][:8] + "..."
                print(f"[JARVIS] Bridge auth:  Bearer {token_preview}")
            else:
                print(f"[JARVIS] Bridge auth:  NONE (open access)")


        # Keep running until SIGTERM (graceful shutdown)
        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: stop_event.set())
        print("[JARVIS] Server running. SIGTERM to stop.")
        await stop_event.wait()

        # Graceful cleanup
        print("[JARVIS] Shutting down...")
        for ws in list(self.clients):
            try: await ws.close()
            except: pass
        self._server_mic_running = False
        await runner.cleanup()
        # Remove PID file
        try: os.unlink("/tmp/jarvis-server.pid")
        except: pass
        print("[JARVIS] Server stopped.")


def main():
    server = JarvisWebServer()
    try:
        asyncio.run(server.run())
    except KeyboardInterrupt:
        print("\n[JARVIS] Shutting down.")


if __name__ == "__main__":
    main()
