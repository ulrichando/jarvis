"""
Voice stream speech-to-text client for push-to-talk.

Connects to a WebSocket endpoint for real-time speech-to-text
transcription. Designed for hold-to-talk: hold the keybinding
to record, release to stop and submit.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Literal, Optional

logger = logging.getLogger(__name__)

KEEPALIVE_MSG = '{"type":"KeepAlive"}'
CLOSE_STREAM_MSG = '{"type":"CloseStream"}'
VOICE_STREAM_PATH = "/api/ws/speech_to_text/voice_stream"
KEEPALIVE_INTERVAL_S = 8.0

# Finalize resolution timeouts
FINALIZE_TIMEOUTS_S = {
    "safety": 5.0,
    "no_data": 1.5,
}

FinalizeSource = Literal[
    "post_closestream_endpoint",
    "no_data_timeout",
    "safety_timeout",
    "ws_close",
    "ws_already_closed",
]


@dataclass
class VoiceStreamCallbacks:
    on_transcript: Callable[[str, bool], None]  # (text, is_final)
    on_error: Callable[[str], None]
    on_close: Callable[[], None]
    on_ready: Callable[["VoiceStreamConnection"], None]


class VoiceStreamConnection:
    """Manages a WebSocket connection for voice streaming STT."""

    def __init__(self, ws: Any, callbacks: VoiceStreamCallbacks):
        self._ws = ws
        self._callbacks = callbacks
        self._connected = False
        self._finalized = False
        self._finalizing = False
        self._keepalive_task: Optional[asyncio.Task] = None
        self._resolve_finalize: Optional[Callable[[FinalizeSource], None]] = None
        self._last_transcript_text = ""

    def send(self, audio_chunk: bytes) -> None:
        """Send an audio chunk over the WebSocket."""
        if self._finalized:
            logger.debug(
                f"[voice_stream] Dropping audio after CloseStream: {len(audio_chunk)} bytes"
            )
            return
        if not self._connected:
            return
        try:
            asyncio.get_event_loop().create_task(self._ws.send(audio_chunk))
        except Exception:
            pass

    async def finalize(self) -> FinalizeSource:
        """Signal end of audio stream and wait for final transcript."""
        if self._finalizing or self._finalized:
            return "ws_already_closed"

        self._finalizing = True

        loop = asyncio.get_event_loop()
        future: asyncio.Future[FinalizeSource] = loop.create_future()

        def resolve(source: FinalizeSource) -> None:
            if not future.done():
                # Promote any remaining interim transcript
                if self._last_transcript_text:
                    text = self._last_transcript_text
                    self._last_transcript_text = ""
                    self._callbacks.on_transcript(text, True)
                future.set_result(source)

        self._resolve_finalize = resolve

        # Set safety timeout
        async def _safety_timeout():
            await asyncio.sleep(FINALIZE_TIMEOUTS_S["safety"])
            resolve("safety_timeout")

        asyncio.ensure_future(_safety_timeout())

        # Set no-data timeout
        async def _no_data_timeout():
            await asyncio.sleep(FINALIZE_TIMEOUTS_S["no_data"])
            resolve("no_data_timeout")

        asyncio.ensure_future(_no_data_timeout())

        # Send CloseStream
        self._finalized = True
        try:
            await self._ws.send(CLOSE_STREAM_MSG)
        except Exception:
            pass

        return await future

    def close(self) -> None:
        """Close the connection."""
        self._finalized = True
        self._connected = False
        if self._keepalive_task:
            self._keepalive_task.cancel()
            self._keepalive_task = None
        try:
            asyncio.get_event_loop().create_task(self._ws.close())
        except Exception:
            pass

    def is_connected(self) -> bool:
        return self._connected

    def _handle_message(self, raw: str) -> None:
        """Handle incoming WebSocket message."""
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        msg_type = msg.get("type")

        if msg_type == "TranscriptText":
            transcript = msg.get("data", "")
            if transcript:
                self._last_transcript_text = transcript
                self._callbacks.on_transcript(transcript, False)

        elif msg_type == "TranscriptEndpoint":
            final_text = self._last_transcript_text
            self._last_transcript_text = ""
            if final_text:
                self._callbacks.on_transcript(final_text, True)
            if self._finalized and self._resolve_finalize:
                self._resolve_finalize("post_closestream_endpoint")

        elif msg_type == "TranscriptError":
            desc = msg.get("description") or msg.get("error_code") or "unknown error"
            if not self._finalizing:
                self._callbacks.on_error(desc)

        elif msg_type == "error":
            detail = msg.get("message", json.dumps(msg))
            if not self._finalizing:
                self._callbacks.on_error(detail)


def is_voice_stream_available() -> bool:
    """Check if voice stream STT is available."""
    # Check for required auth tokens
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OAUTH_ACCESS_TOKEN"))


async def connect_voice_stream(
    callbacks: VoiceStreamCallbacks,
    language: str = "en",
    keyterms: Optional[List[str]] = None,
) -> Optional[VoiceStreamConnection]:
    """Connect to the voice stream WebSocket endpoint.

    Returns a VoiceStreamConnection or None if connection fails.
    """
    try:
        import websockets

        base_url = os.environ.get(
            "VOICE_STREAM_BASE_URL", "wss://api.anthropic.com"
        )

        params = {
            "encoding": "linear16",
            "sample_rate": "16000",
            "channels": "1",
            "endpointing_ms": "300",
            "utterance_end_ms": "1000",
            "language": language,
        }

        query = "&".join(f"{k}={v}" for k, v in params.items())
        if keyterms:
            for term in keyterms:
                query += f"&keyterms={term}"

        url = f"{base_url}{VOICE_STREAM_PATH}?{query}"

        headers = {}
        access_token = os.environ.get("OAUTH_ACCESS_TOKEN")
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        elif api_key:
            headers["x-api-key"] = api_key

        ws = await websockets.connect(url, additional_headers=headers)

        connection = VoiceStreamConnection(ws, callbacks)
        connection._connected = True

        # Send initial keepalive
        await ws.send(KEEPALIVE_MSG)

        # Start keepalive task
        async def _keepalive_loop():
            while connection._connected:
                await asyncio.sleep(KEEPALIVE_INTERVAL_S)
                if connection._connected:
                    try:
                        await ws.send(KEEPALIVE_MSG)
                    except Exception:
                        break

        connection._keepalive_task = asyncio.ensure_future(_keepalive_loop())

        # Start message receive loop
        async def _receive_loop():
            try:
                async for message in ws:
                    text = message if isinstance(message, str) else message.decode()
                    connection._handle_message(text)
            except Exception:
                pass
            finally:
                connection._connected = False
                if connection._last_transcript_text:
                    text = connection._last_transcript_text
                    connection._last_transcript_text = ""
                    callbacks.on_transcript(text, True)
                if connection._resolve_finalize:
                    connection._resolve_finalize("ws_close")
                callbacks.on_close()

        asyncio.ensure_future(_receive_loop())
        callbacks.on_ready(connection)
        return connection

    except ImportError:
        logger.warning("websockets package not installed; voice stream unavailable")
        return None
    except Exception as e:
        logger.error(f"Failed to connect voice stream: {e}")
        return None
