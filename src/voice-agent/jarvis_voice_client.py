"""
JARVIS native voice client — LiveKit peer running outside the Tauri webview.

Why this file exists:
    WebKitGTK 2.50.6 on Kali has WebRTC compiled but gates RTCPeerConnection
    behind an internal runtime-feature flag that's not reachable from
    `webkit2gtk-4.1` settings or the Tauri 2 config. Our in-webview
    LiveKit client therefore can't construct a PeerConnection. Production
    desktop voice apps (OpenAI ChatGPT Voice, Siri, etc.) avoid this class
    of problem by doing audio I/O at the OS level, not through a browser
    webview. This process is that layer for JARVIS.

Architecture:
    mic (PipeWire → mic_aec) ──(sounddevice)──▶ rtc.AudioSource
                                                    │
                                                    ▼
                                         rtc.Room (LiveKit peer)
                                                    ▲
                                                    │
                                       agent audio track ◀── jarvis_agent.py
                                                    │
                                                    ▼
                                         rtc.AudioStream  ──(sounddevice)──▶
                                         speaker (PipeWire → sink_aec)

Same SFU, same agent. The only thing that changed vs the failed webview
approach is that the mic capture + speaker playback are native (using
PortAudio → ALSA → PipeWire) instead of through the browser's WebRTC
stack.

Run:
    python jarvis_voice_client.py              # interactive, foreground
    systemctl --user start jarvis-voice-client # headless / auto-restart

Env (from voice-agent/.env, inherited by the systemd unit):
    LIVEKIT_URL           ws://127.0.0.1:7880
    LIVEKIT_API_KEY       matches livekit.yaml
    LIVEKIT_API_SECRET    matches livekit.yaml
    JARVIS_VOICE_IDENTITY desktop-ulrich (default)
    JARVIS_VOICE_ROOM     jarvis         (default)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
from dataclasses import dataclass, asdict
from typing import Optional

import numpy as np
import sounddevice as sd
from aiohttp import web
from livekit import api, rtc

logging.basicConfig(
    level=os.environ.get("JARVIS_VOICE_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
)
log = logging.getLogger("voice-client")

URL          = os.environ.get("LIVEKIT_URL",        "ws://127.0.0.1:7880")
API_KEY      = os.environ.get("LIVEKIT_API_KEY",    "")
API_SECRET   = os.environ.get("LIVEKIT_API_SECRET", "")
IDENTITY     = os.environ.get("JARVIS_VOICE_IDENTITY", "desktop-ulrich")
ROOM_NAME    = os.environ.get("JARVIS_VOICE_ROOM",     "jarvis")

# 48 kHz mono matches both Orpheus TTS output and what sink_aec / mic_aec
# expose in PipeWire. 10 ms frames (480 samples) is the typical WebRTC
# packetisation; keeps latency low and plays nicely with the SFU's
# Opus encoder.
SAMPLE_RATE   = 48_000
NUM_CHANNELS  = 1
FRAME_MS      = 10
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000  # 480

# Small HTTP control plane the Tauri UI (and any future client) polls
# to know what this voice session is doing. Kept on a distinct port
# so we don't collide with bridge (8765) / speech sidecar (8766).
STATUS_PORT   = int(os.environ.get("JARVIS_VOICE_CLIENT_PORT", "8767"))


@dataclass
class ClientState:
    """
    Snapshot the Tauri UI polls. Updated from LiveKit room events +
    mic mute toggles. Deliberately flat so the JSON is trivial to
    reason about on the UI side.
    """
    connected: bool = False           # SFU connection alive
    muted:     bool = False           # local mic track muted
    listening: bool = False           # local speaker (us) is talking
    speaking:  bool = False           # remote agent is talking
    # Informative only — lets the UI show "jarvis@ws://..." if it
    # wants. Populated once on connect.
    url:       Optional[str] = None
    identity:  Optional[str] = None
    room:      Optional[str] = None


state = ClientState()

# Set after publish_track so the /mute handler can toggle. None when
# no room is currently connected.
_mic_pub_ref: Optional[rtc.LocalTrackPublication] = None
# Set inside run_once(); lets /speak publish data packets without
# every handler having to carry the Room reference around.
_room_ref: Optional[rtc.Room] = None


# ── Tiny HTTP control plane ────────────────────────────────────────────

async def _h_status(_: web.Request) -> web.Response:
    """GET /status — snapshot of the current client state."""
    return web.json_response(asdict(state), headers={
        # Permissive CORS so the Tauri webview can poll us from its
        # tauri://localhost origin without preflight headaches.
        "Access-Control-Allow-Origin":  "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    })


async def _h_mute(req: web.Request) -> web.Response:
    """
    POST /mute  body={mute: bool}  → toggle the local mic track mute.

    We mute at the track-publication layer rather than stopping the
    PortAudio stream so re-joining is instant (no sample-rate /
    device re-open latency). LiveKit carries the mute bit to the
    agent, which stops running STT on our (now-silent) audio.
    """
    if _mic_pub_ref is None:
        return web.json_response({"error": "not connected"}, status=503)
    try:
        body = await req.json()
    except Exception:
        body = {}
    target = bool(body.get("mute", not state.muted))  # default = toggle
    try:
        # LocalAudioTrack.mute/unmute are sync in livekit-rtc Python —
        # they only flip a flag that the engine picks up on the next
        # audio frame. No await.
        if target:
            _mic_pub_ref.track.mute()
        else:
            _mic_pub_ref.track.unmute()
        state.muted = target
        return web.json_response({"muted": target}, headers={
            "Access-Control-Allow-Origin": "*",
        })
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def _h_speak(req: web.Request) -> web.Response:
    """
    POST /speak {text} → ask the agent to voice `text` via its TTS.

    Under the hood we publish a LiveKit data-channel message that the
    agent is listening for (see jarvis_agent.py's data_received
    handler). The agent calls session.say(text) which streams TTS
    through the same audio track the conversation uses, so playback
    is a no-op on our side — we already subscribe to that track.

    Used by the Tauri UI when a typed CLI message comes in over the
    bridge WS and needs to be spoken aloud.
    """
    if _room_ref is None or not state.connected:
        return web.json_response({"error": "not connected"}, status=503)
    try:
        body = await req.json()
    except Exception:
        body = {}
    text = (body.get("text") or "").strip()
    if not text:
        return web.json_response({"error": "missing text"}, status=400)
    try:
        payload = json.dumps({"type": "speak", "text": text}).encode("utf-8")
        await _room_ref.local_participant.publish_data(payload, reliable=True)
        return web.json_response({"queued": True, "chars": len(text)}, headers={
            "Access-Control-Allow-Origin": "*",
        })
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def _h_stop(_: web.Request) -> web.Response:
    """POST /stop → ask the agent to interrupt its current utterance."""
    if _room_ref is None or not state.connected:
        return web.json_response({"error": "not connected"}, status=503)
    try:
        payload = json.dumps({"type": "stop"}).encode("utf-8")
        await _room_ref.local_participant.publish_data(payload, reliable=True)
        return web.json_response({"stopped": True}, headers={
            "Access-Control-Allow-Origin": "*",
        })
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def _h_cors(_: web.Request) -> web.Response:
    """OPTIONS preflight for any /... route."""
    return web.Response(status=204, headers={
        "Access-Control-Allow-Origin":  "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
        "Access-Control-Max-Age":       "86400",
    })


def _build_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/status",  _h_status)
    app.router.add_get("/health",  _h_status)   # so systemd / launch.sh can probe
    app.router.add_post("/mute",   _h_mute)
    app.router.add_post("/speak",  _h_speak)
    app.router.add_post("/stop",   _h_stop)
    app.router.add_route("OPTIONS", "/{tail:.*}", _h_cors)
    return app


async def start_http_server() -> web.AppRunner:
    """Bring up the status HTTP server alongside the LiveKit loop."""
    app = _build_app()
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", STATUS_PORT)
    await site.start()
    log.info(f"[http] status/command server on :{STATUS_PORT}")
    return runner


def mint_token() -> str:
    """
    Mint a LiveKit JWT in-process. The bridge has a /api/livekit/token
    endpoint for the (now-shelved) webview client; here we already have
    the API secret in env, so we skip the HTTP round-trip.
    """
    if not API_KEY or not API_SECRET:
        log.error("LIVEKIT_API_KEY / LIVEKIT_API_SECRET not set — refusing to start")
        sys.exit(2)
    return (
        api.AccessToken(API_KEY, API_SECRET)
        .with_identity(IDENTITY)
        .with_name("Ulrich (desktop)")
        .with_grants(api.VideoGrants(
            room_join=True,
            room=ROOM_NAME,
            can_publish=True,
            can_subscribe=True,
        ))
        .to_jwt()
    )


async def play_subscribed_track(track: rtc.RemoteAudioTrack) -> None:
    """
    Pipe a subscribed audio track straight to the default output device
    (which PipeWire routes to sink_aec → real speaker). AudioStream is
    given sample_rate / num_channels so livekit-rtc resamples internally
    and every frame we receive matches the OutputStream we open once.
    """
    stream = rtc.AudioStream(
        track,
        sample_rate=SAMPLE_RATE,
        num_channels=NUM_CHANNELS,
    )
    out = sd.OutputStream(
        samplerate=SAMPLE_RATE,
        channels=NUM_CHANNELS,
        dtype="int16",
        # Keep latency low — we don't need a big ring buffer for voice.
        blocksize=FRAME_SAMPLES,
        latency="low",
    )
    out.start()
    log.info(f"[playback] OPEN track={track.sid} sr={SAMPLE_RATE}Hz ch={NUM_CHANNELS}")
    try:
        async for event in stream:
            frame = event.frame
            # frame.data is a bytes-like buffer of int16 LE samples.
            # Reshape (samples, channels) for sounddevice.
            pcm = np.frombuffer(frame.data, dtype=np.int16).reshape(-1, NUM_CHANNELS)
            # write() is non-blocking-ish — it copies into PortAudio's
            # internal ring, the audio thread drains. If we ever fall
            # behind, it returns a buffer-underflow warning; harmless
            # enough for a conversational pace.
            out.write(pcm)
    except Exception as e:
        log.warning(f"[playback] stream error: {e}")
    finally:
        out.stop()
        out.close()
        log.info(f"[playback] CLOSE track={track.sid}")


async def run_once(shutdown: asyncio.Event) -> None:
    """
    One connection attempt. Returns when the SFU disconnects us or
    `shutdown` fires. The outer loop re-connects if we exited
    unexpectedly.
    """
    # Declare module-globals up front so every assignment inside this
    # function (mic publish, room-ref in the finally, etc.) is
    # unambiguous. Python requires `global` to precede any assignment
    # to the name within the function body.
    global _mic_pub_ref, _room_ref
    token = mint_token()
    room = rtc.Room()
    loop = asyncio.get_running_loop()

    # ── Room event handlers update the shared ClientState snapshot
    # used by the /status HTTP endpoint. Keeping the updates here (not
    # inside the LiveKit callback threads) means the HTTP handler sees
    # a coherent view on every poll without any explicit locking.

    @room.on("track_subscribed")
    def _on_track(track: rtc.Track, *_args) -> None:
        # Only agent audio matters. Remote agent publishes SOURCE_MICROPHONE
        # regardless of it being TTS (LiveKit has no distinct 'tts' source).
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            asyncio.create_task(play_subscribed_track(track))

    @room.on("active_speakers_changed")
    def _on_speakers(speakers) -> None:
        # LiveKit flags a participant as an "active speaker" when their
        # VU crosses a threshold. Use this for the listening/speaking
        # indicator on the Tauri UI side — no need for a separate VAD
        # in-client because the SFU already computes it centrally.
        local_active  = any(p.identity == IDENTITY for p in speakers)
        remote_active = any(p.identity != IDENTITY for p in speakers)
        state.listening = local_active
        state.speaking  = remote_active

    @room.on("disconnected")
    def _on_disc(reason: rtc.DisconnectReason) -> None:
        log.warning(f"[room] disconnected reason={reason}")
        state.connected = False
        state.listening = False
        state.speaking  = False
        shutdown.set()

    log.info(f"connecting url={URL} room={ROOM_NAME} identity={IDENTITY}")
    await room.connect(URL, token, options=rtc.RoomOptions(auto_subscribe=True))
    log.info("[room] connected")
    state.connected = True
    state.url       = URL
    state.identity  = IDENTITY
    state.room      = ROOM_NAME
    # Expose the room so the /speak + /stop handlers can publish
    # data packets to it. Cleared in the finally: block below.
    _room_ref = room

    # Publish mic. Must be done AFTER connect — the AudioSource isn't
    # known to the SFU until the track is created + published.
    source = rtc.AudioSource(SAMPLE_RATE, NUM_CHANNELS)
    mic_track = rtc.LocalAudioTrack.create_audio_track("mic", source)
    mic_pub = await room.local_participant.publish_track(
        mic_track,
        rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE),
    )
    log.info("[mic] track published")

    # Expose mic_pub via a closure so the HTTP mute handler can flip
    # it. Global-ish state here is pragmatic — there's only ever one
    # active room per process, and the alternative (passing refs
    # through every handler) is much noisier.
    _mic_pub_ref = mic_pub

    # PortAudio callback runs in a realtime thread. Marshal each frame
    # back to the asyncio loop so capture_frame (which awaits) runs on
    # the right thread. run_coroutine_threadsafe is exactly that bridge.
    def _mic_cb(indata, frames, _time, status) -> None:
        if status:
            log.debug(f"[mic] portaudio status: {status}")
        frame = rtc.AudioFrame(
            data=indata.tobytes(),
            sample_rate=SAMPLE_RATE,
            num_channels=NUM_CHANNELS,
            samples_per_channel=frames,
        )
        asyncio.run_coroutine_threadsafe(source.capture_frame(frame), loop)

    mic_stream = sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=NUM_CHANNELS,
        dtype="int16",
        blocksize=FRAME_SAMPLES,
        callback=_mic_cb,
        latency="low",
    )
    mic_stream.start()
    log.info("[mic] capture started")

    try:
        await shutdown.wait()
    finally:
        log.info("tearing down")
        _room_ref = None
        _mic_pub_ref = None
        mic_stream.stop()
        mic_stream.close()
        await room.disconnect()


async def main() -> None:
    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, shutdown.set)

    # HTTP control plane runs for the whole process lifetime — survives
    # LiveKit reconnects so the Tauri UI gets a quick "connected=false"
    # during a blip rather than a 404.
    http_runner = await start_http_server()

    # Supervisor loop — reconnect with 2 s backoff on transient errors
    # so a blip in the SFU (or a reboot) doesn't leave the client dead.
    # Systemd also restarts the process if it exits, so this is a
    # belt-and-braces measure for soft errors that don't kill the process.
    backoff = 2.0
    while not shutdown.is_set():
        try:
            await run_once(shutdown)
            # Clean exit → reset backoff + loop immediately
            backoff = 2.0
        except Exception as e:
            log.exception(f"connection attempt failed: {e}")
            try:
                await asyncio.wait_for(shutdown.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, 30.0)

    await http_runner.cleanup()
    log.info("bye")


if __name__ == "__main__":
    asyncio.run(main())
