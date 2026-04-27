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
import threading
import time
from dataclasses import dataclass, asdict
from pathlib import Path
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

# ── Asyncio loop watchdog ────────────────────────────────────────────
# We've seen the asyncio loop stall every few hours: HTTP server on
# :8767 stops responding (curl times out), tray + UI pill desync from
# the actual room state, and the only fix is `systemctl restart
# jarvis-voice-client`. Root causes vary (LiveKit FFI callbacks doing
# heavy work synchronously, sounddevice GIL contention, …) but the
# symptom is always: the loop stops servicing tasks.
#
# Fix: an OS-thread watchdog (NOT an asyncio task — those wouldn't run
# either when the loop is stuck). The asyncio side updates a shared
# timestamp every WATCHDOG_HEARTBEAT_SEC; the OS thread polls that
# timestamp every WATCHDOG_POLL_SEC. If the heartbeat goes stale by
# more than WATCHDOG_STALE_SEC, the thread os._exit(1)'s the process
# so systemd's Restart=on-failure brings up a fresh, leak-free copy.
WATCHDOG_HEARTBEAT_SEC = 5.0
WATCHDOG_POLL_SEC      = 10.0
WATCHDOG_STALE_SEC     = 60.0

_last_heartbeat: float = time.monotonic()
_heartbeat_lock = threading.Lock()


async def _heartbeat_loop(shutdown: asyncio.Event) -> None:
    """Asyncio task: stamps the shared timestamp every few seconds.
    The watchdog OS thread checks this timestamp; if it goes stale,
    it kills the process."""
    global _last_heartbeat
    while not shutdown.is_set():
        with _heartbeat_lock:
            _last_heartbeat = time.monotonic()
        try:
            await asyncio.wait_for(shutdown.wait(), timeout=WATCHDOG_HEARTBEAT_SEC)
        except asyncio.TimeoutError:
            pass


# How long to wait for agent_present before assuming the agent worker
# missed the dispatch (race: client connected before worker registered).
# Self-heal by restarting so the preflight delete_room forces a fresh
# dispatch into the now-registered worker.
AGENT_DISPATCH_TIMEOUT_SEC = 45.0


async def _agent_presence_watchdog(shutdown: asyncio.Event) -> None:
    """If we're connected but agent_present stays False for too long,
    the SFU never dispatched a job (timing race between agent restart
    and our room connection). Restart ourselves to force a fresh dispatch."""
    # Give a grace window from startup — the SFU can take a few seconds
    # to route the job even under normal conditions.
    await asyncio.sleep(AGENT_DISPATCH_TIMEOUT_SEC)
    while not shutdown.is_set():
        if state.connected and not state.agent_present:
            log.warning(
                f"[presence-watchdog] connected but no agent after "
                f"{AGENT_DISPATCH_TIMEOUT_SEC:.0f}s — restarting to force dispatch"
            )
            try:
                await asyncio.create_subprocess_exec(
                    "systemctl", "--user", "restart", "jarvis-voice-client",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
            except Exception as e:
                log.warning(f"[presence-watchdog] restart failed: {e}")
            return
        try:
            await asyncio.wait_for(shutdown.wait(), timeout=15.0)
        except asyncio.TimeoutError:
            pass


def _watchdog_thread() -> None:
    """OS thread: kills the process if the asyncio loop stops
    updating the heartbeat. Daemon so it doesn't block normal exit."""
    # First update happens after the heartbeat task starts; give it
    # a generous grace window before we'd ever consider firing.
    grace_until = time.monotonic() + WATCHDOG_STALE_SEC + 30
    while True:
        time.sleep(WATCHDOG_POLL_SEC)
        if time.monotonic() < grace_until:
            continue
        with _heartbeat_lock:
            age = time.monotonic() - _last_heartbeat
        if age > WATCHDOG_STALE_SEC:
            log.error(
                f"[watchdog] asyncio loop heartbeat stale ({age:.0f}s old) — "
                f"killing process so systemd restarts us"
            )
            # os._exit (not sys.exit) — the loop is dead, atexit
            # handlers would deadlock waiting on it.
            os._exit(1)

# Small HTTP control plane the Tauri UI (and any future client) polls
# to know what this voice session is doing. Kept on a distinct port
# so we don't collide with bridge (8765) / speech sidecar (8766).
STATUS_PORT   = int(os.environ.get("JARVIS_VOICE_CLIENT_PORT", "8767"))

# CLI-model switching. The tray POSTs to /cli-model; we write the
# chosen model ID to this file. The voice-agent's run_jarvis_cli
# reads the file on every spawn and exports JARVIS_PROVIDER +
# JARVIS_MODEL to the CLI subprocess — so switching takes effect on
# the very next tool call, no restart required. start.sh also reads
# this file so interactive terminal sessions stay in sync.
CLI_MODEL_FILE      = Path.home() / ".jarvis" / "cli-model"
DEFAULT_CLI_MODEL   = "deepseek-v4-pro"

# Speech-LLM (voice-side) switching. Same file/endpoint pattern as
# CLI model but a switch DOES require a restart of the agent unit
# (its LLM is built once at session start; can't hot-swap). voice-
# client kicks `systemctl --user restart jarvis-voice-agent` after
# writing the file. The voice-client itself stays up — the SFU
# preserves the room while the agent rejoins.
SPEECH_MODEL_FILE      = Path.home() / ".jarvis" / "voice-model"

# Same path as jarvis_agent.py's _TOOL_BUSY_FILE — written when a
# tool starts, deleted when it ends. Voice-client polls existence
# (cheap stat call) on every /status hit.
TOOL_BUSY_FILE         = Path.home() / ".jarvis" / ".tool-running"

# Agent's LLM-thinking flag. Same pattern: present means LLM is
# generating. Has a staleness check below — if the file is older
# than AGENT_THINKING_MAX_AGE we ignore it. This also handles the
# "agent decided to stay silent" case (the directed-at-me filter
# rejects an ambient mic trigger): no assistant turn ever lands to
# clear the flag, but it goes stale within a few seconds and the
# tray drops gold automatically. 10 s is generous for real LLM
# thinking; long-running TOOL calls use the separate tool_running
# flag (no time limit on that one).
AGENT_THINKING_FILE    = Path.home() / ".jarvis" / ".agent-thinking"
AGENT_THINKING_MAX_AGE = 10   # seconds


def _agent_is_thinking() -> bool:
    """True if the thinking flag file exists AND is recent enough."""
    try:
        # Use mtime — the agent rewrites the file on each new turn,
        # so stat is enough; we don't need to read the contents.
        age = time.time() - AGENT_THINKING_FILE.stat().st_mtime
        return age < AGENT_THINKING_MAX_AGE
    except FileNotFoundError:
        return False
    except Exception:
        return False
DEFAULT_SPEECH_MODEL   = "llama-3.3-70b-versatile"
SPEECH_MODELS_AVAILABLE = (
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "qwen/qwen3-32b",
    "openai/gpt-oss-120b",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    # DeepSeek removed — see comment in jarvis_agent.py SPEECH_MODELS:
    # the openai plugin doesn't echo `reasoning_content` so multi-
    # turn DeepSeek conversations 400 every turn after the first.
)


def _read_speech_model() -> str:
    try:
        name = SPEECH_MODEL_FILE.read_text(encoding="utf-8").strip()
        if name in SPEECH_MODELS_AVAILABLE:
            return name
    except FileNotFoundError:
        pass
    except Exception as e:
        log.warning(f"could not read {SPEECH_MODEL_FILE}: {e}")
    return DEFAULT_SPEECH_MODEL


async def _restart_agent_unit() -> None:
    """Bounce both jarvis-voice-agent (to rebuild the LLM with the
    new voice-model) AND ourselves a moment later (so the voice-
    client's preflight delete_room forces LiveKit to dispatch a
    FRESH job into the freshly-restarted agent — without this, the
    SFU keeps the existing room, no new dispatch fires, and JARVIS
    sits silent with the old LLM in memory).

    Order matters: agent first → wait for it to re-register (~3 s)
    → then restart self. The HTTP response to the original POST may
    get cut short when self dies; that's expected and harmless,
    the tray's optimistic label update already covered the UX gap.
    """
    try:
        agent_proc = await asyncio.create_subprocess_exec(
            "systemctl", "--user", "restart", "jarvis-voice-agent",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, err = await agent_proc.communicate()
        if agent_proc.returncode != 0:
            log.warning(
                f"agent restart returned {agent_proc.returncode}: "
                f"{err.decode('utf-8', 'ignore').strip()}"
            )
            return
        log.info("agent unit restart kicked, waiting before bouncing self")
    except Exception as e:
        log.warning(f"could not restart agent: {e}")
        return

    # 4 s is enough on this host for Silero VAD prewarm + worker
    # registration; tune up if the agent log shows "registered worker"
    # arrives later.
    await asyncio.sleep(4)

    try:
        await asyncio.create_subprocess_exec(
            "systemctl", "--user", "restart", "jarvis-voice-client",
        )
    except Exception as e:
        log.warning(f"could not restart self: {e}")
# Whitelist mirroring CLI_MODELS in jarvis_agent.py — duplicated as a
# literal tuple so the voice-client doesn't have to import heavy
# livekit plugin machinery just to validate a string.
CLI_MODELS_AVAILABLE = (
    "deepseek-chat",
    "deepseek-reasoner",
    "deepseek-v4-flash",
    "deepseek-v4-pro",
    "qwen/qwen3-32b",
    "llama-3.3-70b-versatile",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "openai/gpt-oss-120b",
)


def _read_cli_model() -> str:
    try:
        name = CLI_MODEL_FILE.read_text(encoding="utf-8").strip()
        if name in CLI_MODELS_AVAILABLE:
            return name
    except FileNotFoundError:
        pass
    except Exception as e:
        log.warning(f"could not read {CLI_MODEL_FILE}: {e}")
    return DEFAULT_CLI_MODEL


@dataclass
class ClientState:
    """
    Snapshot the Tauri UI polls. Updated from LiveKit room events +
    mic mute toggles. Deliberately flat so the JSON is trivial to
    reason about on the UI side.
    """
    connected:     bool = False       # SFU connection alive
    # True once a remote agent participant has actually joined the
    # room. The SFU connection (`connected`) comes up in ~100 ms but
    # the agent worker takes another second or two to accept the job
    # and join. Until `agent_present` flips true, JARVIS can't hear
    # the user. The UI pill uses this to distinguish "voice booting"
    # (amber) from "voice ready" (green).
    agent_present: bool = False
    muted:         bool = False       # local mic track muted
    listening:     bool = False       # local speaker (us) is talking
    speaking:      bool = False       # remote agent is talking
    # Active CLI model ID (e.g., "deepseek-chat", "qwen/qwen3-32b").
    # Read straight from CLI_MODEL_FILE on every /status hit, so the
    # tray sees changes the same instant they're written.
    cli_model:     str = ""
    # Active speech (voice LLM) model ID. Same dynamic-read pattern.
    speech_model:  str = ""
    # True while a tool (run_jarvis_cli) is in flight in the agent.
    # Drives the tray's "thinking" amber for the full duration of
    # background work — without this signal, the inferred-thinking
    # TTL on the desktop side gives up after 12 s and the tray
    # flickers back to green even though JARVIS is still working.
    tool_running:  bool = False
    # True while the agent's LLM is generating a reply. Touched by
    # the agent on user_input_transcribed, removed when the assistant
    # turn lands. Definitive signal — replaces the prior heuristic
    # of inferring thinking from listening→quiet transitions, which
    # gave false positives on every ambient mic trigger.
    agent_thinking: bool = False
    # Informative only — lets the UI show "jarvis@ws://..." if it
    # wants. Populated once on connect.
    url:           Optional[str] = None
    identity:      Optional[str] = None
    room:          Optional[str] = None


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
    # Refresh cli_model + speech_model from disk on every poll. The
    # files are small, reads are cheap, and this avoids any sync-with-
    # tray race.
    state.cli_model    = _read_cli_model()
    state.speech_model = _read_speech_model()
    # Cheap stat call — flag file is touched/removed by the agent's
    # tool wrappers around every run_jarvis_cli call.
    state.tool_running = TOOL_BUSY_FILE.exists()
    # Definitive thinking signal — but only when the agent isn't
    # actively speaking. If TTS is playing we know the agent finished
    # its LLM phase, so suppress agent_thinking even if the file
    # hasn't been cleared yet (avoids gold→blue→gold flicker between
    # `conversation_item_added` and the speaking-track event).
    state.agent_thinking = _agent_is_thinking() and not state.speaking
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


async def _h_user_input(req: web.Request) -> web.Response:
    """
    POST /user-input {text} → inject `text` as a synthetic user turn
    into the active voice session.

    Distinct from /speak: /speak makes JARVIS read text aloud (TTS
    only, no LLM). /user-input feeds the text into the AgentSession
    as if it had come from STT — JARVIS's LLM processes it, generates
    a reply, and the reply gets voiced via TTS. Both the user turn
    and the agent's reply land in conversations.db (and hence Convex
    via the mirror) so a web client subscribed to that session sees
    the round trip live.

    Used by the web voice-transcript page to let the user follow up
    via typing without breaking out a mic.
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
        payload = json.dumps({"type": "user_input", "text": text}).encode("utf-8")
        await _room_ref.local_participant.publish_data(payload, reliable=True)
        return web.json_response({"queued": True, "chars": len(text)}, headers={
            "Access-Control-Allow-Origin": "*",
        })
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def _h_cli_model(req: web.Request) -> web.Response:
    """
    GET  /cli-model                          → {"model": "<id>", "available": [...]}
    POST /cli-model {"model": "deepseek-chat"} → write the choice

    The model ID is whatever the CLI's jarvisModelRegistry.ts knows
    about. The voice-agent's run_jarvis_cli reads the file on every
    spawn, so the change takes effect on the next CLI invocation
    without restarting any process.
    """
    cors = {"Access-Control-Allow-Origin": "*"}
    if req.method == "GET":
        return web.json_response({
            "model":     _read_cli_model(),
            "available": list(CLI_MODELS_AVAILABLE),
        }, headers=cors)

    # POST
    try:
        body = await req.json()
    except Exception:
        body = {}
    name = (body.get("model") or body.get("name") or "").strip()
    if name not in CLI_MODELS_AVAILABLE:
        return web.json_response(
            {"error": f"unknown CLI model: {name!r}",
             "available": list(CLI_MODELS_AVAILABLE)},
            status=400, headers=cors,
        )
    try:
        CLI_MODEL_FILE.parent.mkdir(parents=True, exist_ok=True)
        CLI_MODEL_FILE.write_text(name + "\n", encoding="utf-8")
        state.cli_model = name
        return web.json_response({"model": name}, headers=cors)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500, headers=cors)


async def _h_speech_model(req: web.Request) -> web.Response:
    """
    GET  /voice-model                   → {"model": "<id>", "available": [...]}
    POST /voice-model {"model": "X"}    → write the choice + restart agent

    Switching speech model requires a quick agent restart (~5 s amber
    "JARVIS booting" in the pill) because AgentSession's LLM is built
    once at session start. The voice-client itself stays up — the
    SFU keeps the room alive and the new agent rejoins automatically.
    """
    cors = {"Access-Control-Allow-Origin": "*"}
    if req.method == "GET":
        return web.json_response({
            "model":     _read_speech_model(),
            "available": list(SPEECH_MODELS_AVAILABLE),
        }, headers=cors)

    # POST
    try:
        body = await req.json()
    except Exception:
        body = {}
    name = (body.get("model") or body.get("name") or "").strip()
    if name not in SPEECH_MODELS_AVAILABLE:
        return web.json_response(
            {"error": f"unknown speech model: {name!r}",
             "available": list(SPEECH_MODELS_AVAILABLE)},
            status=400, headers=cors,
        )
    try:
        SPEECH_MODEL_FILE.parent.mkdir(parents=True, exist_ok=True)
        SPEECH_MODEL_FILE.write_text(name + "\n", encoding="utf-8")
        state.speech_model = name
        # Fire-and-forget — agent restart takes ~3-5 s; the user sees
        # the pill flip to amber "JARVIS booting" and back to green.
        asyncio.create_task(_restart_agent_unit())
        return web.json_response(
            {"model": name, "restarting": True}, headers=cors,
        )
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500, headers=cors)


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
    app.router.add_post("/speak",      _h_speak)
    app.router.add_post("/stop",       _h_stop)
    app.router.add_post("/user-input", _h_user_input)
    app.router.add_get("/cli-model",   _h_cli_model)
    app.router.add_post("/cli-model",  _h_cli_model)
    app.router.add_get("/voice-model", _h_speech_model)
    app.router.add_post("/voice-model", _h_speech_model)
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

    @room.on("participant_connected")
    def _on_participant_connected(participant: rtc.RemoteParticipant) -> None:
        # The agent worker joins as a remote participant with an
        # `agent-…` identity. Flip agent_present on so the UI pill
        # can switch from "booting" (amber) to "ready" (green).
        # We don't filter by kind — in this setup the only remote
        # that should ever join is the agent.
        log.info(f"[room] participant joined: {participant.identity}")
        state.agent_present = True

    @room.on("participant_disconnected")
    def _on_participant_disconnected(participant: rtc.RemoteParticipant) -> None:
        log.info(f"[room] participant left: {participant.identity}")
        # Re-evaluate: if no remote participants remain, we're solo.
        remaining = [p for p in room.remote_participants.values()]
        state.agent_present = len(remaining) > 0

    @room.on("disconnected")
    def _on_disc(reason: rtc.DisconnectReason) -> None:
        log.warning(f"[room] disconnected reason={reason}")
        state.connected     = False
        state.agent_present = False
        state.listening     = False
        state.speaking      = False
        shutdown.set()

    # ── Stream drain handlers ───────────────────────────────────────
    # The agent publishes two streams the voice-client doesn't
    # consume directly:
    #   • lk.agent.session   (byte) — session state / token chunks
    #   • lk.transcription   (text) — STT/TTS transcripts (UI gets
    #                                 these from the agent's chat
    #                                 channel instead, not here)
    #
    # With no handler registered, the LiveKit FFI logs
    # `ignoring byte stream with topic '…', no callback attached`
    # for every chunk and drops it. The log line + drop runs on the
    # asyncio loop. Under heavy traffic (long replies = many chunks
    # per turn, long sessions = more turns) the queue grows faster
    # than the loop drains, the HTTP server on :8767 stops
    # responding, and the desktop pill desyncs from the tray icon
    # while curl times out — the recurring ~30-45 min hang.
    #
    # Fix: register sync wrappers that schedule the actual async drain
    # via asyncio.create_task. The SDK calls byte/text stream handlers
    # synchronously (room.py:979 invokes `handler(reader, identity)`
    # without await), so handing it a coroutine directly leaks it
    # uncawaited and never reads the buffer. Wrapping in create_task
    # both satisfies the sync-call contract AND drains the reader.
    async def _drain_byte_stream(reader, participant_identity: str) -> None:
        try:
            async for _ in reader:
                pass
        except Exception as e:
            log.debug(f"[stream-drain] byte stream from {participant_identity} ended: {e}")

    async def _drain_text_stream(reader, participant_identity: str) -> None:
        try:
            async for _ in reader:
                pass
        except Exception as e:
            log.debug(f"[stream-drain] text stream from {participant_identity} ended: {e}")

    def _byte_stream_handler(reader, participant_identity: str) -> None:
        loop.create_task(_drain_byte_stream(reader, participant_identity))

    def _text_stream_handler(reader, participant_identity: str) -> None:
        loop.create_task(_drain_text_stream(reader, participant_identity))

    room.register_byte_stream_handler("lk.agent.session", _byte_stream_handler)
    room.register_text_stream_handler("lk.transcription", _text_stream_handler)

    # Pre-flight: delete any leftover "jarvis" room from a previous
    # process life. Why this is necessary: when the agent worker is
    # restarted (reboot, systemctl restart), its session ends abruptly
    # and the SFU keeps a ghost agent-participant in the room for a
    # TTL. On reconnect the client joins the EXISTING room, no fresh
    # agent dispatch fires, and JARVIS appears silent forever. A
    # delete-and-recreate on startup sidesteps this entirely — the
    # client then joins a brand-new room, LiveKit dispatches a worker,
    # greeting/tool/voice loop all come up clean.
    #
    # Safe because the voice-client is the ONLY long-lived participant
    # we run today (the phone client is future work). If you add
    # concurrent participants, gate this on "room exists but has no
    # live humans" instead.
    try:
        lkapi = api.LiveKitAPI(
            URL.replace("ws://", "http://").replace("wss://", "https://"),
            API_KEY,
            API_SECRET,
        )
        try:
            await lkapi.room.delete_room(api.DeleteRoomRequest(room=ROOM_NAME))
            log.info(f"[preflight] cleared stale room {ROOM_NAME}")
        except Exception as e:
            # Room didn't exist — that's the happy path, ignore.
            log.debug(f"[preflight] room delete (expected if fresh): {e}")
        await lkapi.aclose()
    except Exception as e:
        log.warning(f"[preflight] room cleanup skipped: {e}")

    log.info(f"connecting url={URL} room={ROOM_NAME} identity={IDENTITY}")
    await room.connect(URL, token, options=rtc.RoomOptions(auto_subscribe=True))
    log.info("[room] connected")
    state.connected = True
    state.url       = URL
    state.identity  = IDENTITY
    state.room      = ROOM_NAME
    # Seed agent_present in case the agent was already in the room
    # when we connected (unlikely with the preflight-delete above,
    # but a participant_connected event is only delivered for joins
    # AFTER our connection, not for pre-existing participants).
    state.agent_present = len(room.remote_participants) > 0
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

    # Asyncio loop watchdog. The OS thread runs forever (daemon=True
    # so it doesn't block exit); the asyncio task heartbeats every
    # few seconds. If the loop stalls, the thread kills the process
    # and systemd Restart=on-failure brings us back fresh.
    threading.Thread(target=_watchdog_thread, name="loop-watchdog", daemon=True).start()
    asyncio.create_task(_heartbeat_loop(shutdown), name="heartbeat")
    asyncio.create_task(_agent_presence_watchdog(shutdown), name="presence-watchdog")
    log.info(
        f"[watchdog] enabled — heartbeat every {WATCHDOG_HEARTBEAT_SEC}s, "
        f"kill if stale > {WATCHDOG_STALE_SEC}s"
    )

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
