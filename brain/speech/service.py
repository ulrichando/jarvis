"""JARVIS Speech Service — standalone daemon for STT and TTS.

Runs as an independent service, listening for audio input and
providing speech synthesis. Communicates with the brain service
over HTTP.

Endpoints:
    POST /transcribe   — Transcribe audio bytes → {text}
    POST /speak        — Generate TTS audio {text} → audio/mpeg
    GET  /health       — Health check
"""

import asyncio
import io
import signal
import sys
from pathlib import Path

from aiohttp import web

_jarvis_root = Path(__file__).resolve().parent.parent.parent
if str(_jarvis_root) not in sys.path:
    sys.path.insert(0, str(_jarvis_root))

HOST = "127.0.0.1"
PORT = 8702
TTS_VOICE = "en-US-AndrewMultilingualNeural"


class SpeechService:
    def __init__(self):
        self._stt_ready = False
        self._tts_ready = False

    async def start(self):
        # Pre-load the whisper model
        try:
            from brain.speech.stt import _get_model
            _get_model()
            self._stt_ready = True
            print("[JARVIS Speech] STT model loaded")
        except Exception as e:
            print(f"[JARVIS Speech] STT unavailable: {e}")

        # TTS is always available (edge-tts is cloud, piper/espeak are local)
        self._tts_ready = True
        print("[JARVIS Speech] TTS ready")

    async def health(self, request: web.Request) -> web.Response:
        return web.json_response({
            "status": "ok",
            "service": "jarvis-speech",
            "stt": self._stt_ready,
            "tts": self._tts_ready,
        })

    async def transcribe(self, request: web.Request) -> web.Response:
        if not self._stt_ready:
            return web.json_response({"error": "STT not available"}, status=503)

        audio_bytes = await request.read()
        if not audio_bytes:
            return web.json_response({"error": "no audio data"}, status=400)

        try:
            from brain.speech.stt import audio_bytes_to_numpy, transcribe_audio
            audio_np = audio_bytes_to_numpy(audio_bytes)
            text = transcribe_audio(audio_np, 16000)
            return web.json_response({"text": text})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def speak(self, request: web.Request) -> web.StreamResponse:
        data = await request.json()
        text = data.get("text", "")
        voice = data.get("voice", TTS_VOICE)

        if not text:
            return web.Response(status=400, text="Missing text")

        try:
            import edge_tts
            communicate = edge_tts.Communicate(text, voice)
            audio_data = io.BytesIO()

            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_data.write(chunk["data"])

            audio_data.seek(0)
            return web.Response(
                body=audio_data.read(),
                content_type="audio/mpeg",
                headers={"Cache-Control": "no-cache"},
            )
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)


def main():
    service = SpeechService()

    app = web.Application()
    app.router.add_get("/health", service.health)
    app.router.add_post("/transcribe", service.transcribe)
    app.router.add_post("/speak", service.speak)

    loop = asyncio.new_event_loop()

    def on_shutdown(_sig=None, _frame=None):
        print("[JARVIS Speech] Shutting down...")
        sys.exit(0)

    signal.signal(signal.SIGTERM, on_shutdown)
    signal.signal(signal.SIGINT, on_shutdown)

    loop.run_until_complete(service.start())

    print(f"[JARVIS Speech] Listening on {HOST}:{PORT}")
    web.run_app(app, host=HOST, port=PORT, print=None)


if __name__ == "__main__":
    main()
