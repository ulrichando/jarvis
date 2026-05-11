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
import faulthandler
import json
import logging
import os
import signal
import sys
import threading
import time
import traceback
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

import numpy as np
import sounddevice as sd
from aiohttp import web
from livekit import api, rtc

# Defensive monkey-patch on livekit.rtc.Room — install BEFORE any Room
# is constructed. See src/voice-agent/resilience/track_guard.py and
# spec 2026-05-04-jarvis-voice-resilience-design.md.
import resilience.track_guard as _track_guard
_track_guard.install()

logging.basicConfig(
    level=os.environ.get("JARVIS_VOICE_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
)
log = logging.getLogger("jarvis.voice_client")

# LiveKit auth + room-identity extracted to voice_client_auth.py 2026-05-10
# (Step 7 of the audit). Re-imported so existing references to URL /
# API_KEY / API_SECRET / IDENTITY / ROOM_NAME inside this file still
# resolve.
from voice_client_auth import (
    URL,
    API_KEY,
    API_SECRET,
    IDENTITY,
    ROOM_NAME,
    mint_token,
)

# 48 kHz mono matches both Orpheus TTS output and what sink_aec / mic_aec
# expose in PipeWire. 10 ms frames (480 samples) is the typical WebRTC
# packetisation; keeps latency low and plays nicely with the SFU's
# Opus encoder.
SAMPLE_RATE   = 48_000
NUM_CHANNELS  = 1
FRAME_MS      = 10
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000  # 480

# ── Watchdog (loop wedge + agent presence + stale STT) ──────────
# Extracted to voice_client_watchdog.py 2026-05-10 (Step 7 of the
# audit). The class encapsulates all watchdog state (_last_heartbeat,
# _last_voice_active_ts, _main_loop) so the module-level globals are
# gone. Instantiation happens after `state = ClientState()` below;
# main() starts the OS thread + 3 async tasks, and the room event
# handler calls `_watchdog.mark_voice_active()` on local speech.
from voice_client_watchdog import (
    LoopWatchdog,
    WATCHDOG_HEARTBEAT_SEC,
    WATCHDOG_POLL_SEC,
    WATCHDOG_STALE_SEC,
    AGENT_DISPATCH_TIMEOUT_SEC,
    STALE_STT_SEC,
)

# HTTP control plane extracted to voice_client_http_api.py 2026-05-10
# (Step 7 of the audit). STATUS_PORT lives there now; we import it
# back so the main()-level reference + any external imports stay valid.
from voice_client_http_api import STATUS_PORT, VoiceClientHttpApi

# Screen-share publisher (X11 → LiveKit video). OFF by default; turned
# on via POST /screen-share or the tray toggle. See module docstring.
from voice_client_screen_share import ScreenShare

# Tray-config layer extracted to voice_client_tray_config.py 2026-05-10
# (Step 7 of the audit). Re-exported under legacy underscored names so
# the HTTP handlers + watchdogs stay untouched.
from voice_client_tray_config import (
    CLI_MODEL_FILE,
    DEFAULT_CLI_MODEL,
    CLI_MODELS_AVAILABLE,
    SPEECH_MODEL_FILE,
    DEFAULT_SPEECH_MODEL,
    SPEECH_MODELS_AVAILABLE,
    TTS_PROVIDER_FILE,
    TTS_PROVIDERS_AVAILABLE,
    TOOL_BUSY_FILE,
    SILENT_MODE_FILE,
    AGENT_THINKING_FILE,
    AGENT_THINKING_MAX_AGE,
    default_tts_provider     as _default_tts_provider,
    ensure_tts_provider_file as _ensure_tts_provider_file,
    read_speech_model        as _read_speech_model,
    read_cli_model           as _read_cli_model,
    agent_is_thinking        as _agent_is_thinking,
)


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
    # True when the agent has entered soft-mute / silent mode
    # ("go quiet", "stop listening"). Mic stays on so wake commands
    # still work, but JARVIS won't respond. Distinct from `muted`
    # (hardware track mute). UI maps this to the black indicator.
    silent_mode:   bool = False
    # True while the screen-share publisher is active (ffmpeg piping
    # x11grab frames into a LiveKit video track). Toggled by
    # POST /screen-share. UI uses this to render a "sharing screen"
    # indicator next to the mute pill.
    sharing_screen: bool = False
    # Active TTS provider spec (e.g., "groq:troy").
    # Read from TTS_PROVIDER_FILE on every /status hit.
    tts_provider:  str = ""
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

# Forward-declare; bound after `_restart_agent_unit` is defined (it's
# the callback the stale-STT watchdog needs).
_watchdog: Optional[LoopWatchdog] = None

# Process-wide screen-share publisher. Constructed once at module load
# so the HTTP handler can flip it on/off without worrying about which
# Room instance is live — the publisher itself takes the Room as a
# start() argument, so reconnects don't strand state.
_screen_share = ScreenShare()


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
    """One connection attempt. Returns when the SFU disconnects us
    OR `shutdown` fires. The outer main_loop reconnects via the
    ReconnectLadder if `shutdown` is still unset on return."""
    # Declare module-globals up front so every assignment inside this
    # function (mic publish, room-ref in the finally, etc.) is
    # unambiguous. Python requires `global` to precede any assignment
    # to the name within the function body.
    global _mic_pub_ref, _room_ref
    token = mint_token()
    room = rtc.Room()
    loop = asyncio.get_running_loop()

    # Per-run event for SFU-side disconnects. The process-wide
    # `shutdown` is reserved for SIGTERM/SIGINT — conflating the
    # two would mean a single SFU drop ends the supervisor loop
    # without giving the ReconnectLadder a chance to recover.
    room_disconnected = asyncio.Event()

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
        if local_active and _watchdog is not None:
            _watchdog.mark_voice_active()

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
        room_disconnected.set()

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
        # Block until either the process is shutting down (SIGTERM/INT)
        # or the SFU dropped us. The supervisor loop above decides what
        # to do next based on which fired.
        done, pending = await asyncio.wait(
            [
                asyncio.create_task(shutdown.wait()),
                asyncio.create_task(room_disconnected.wait()),
            ],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
    finally:
        log.info("tearing down")
        # Tear down screen-share BEFORE clearing _room_ref so the
        # publisher's unpublish_track call hits the still-connected room.
        try:
            if _screen_share.is_active():
                await _screen_share.stop()
                state.sharing_screen = False
        except Exception as e:
            log.warning(f"[teardown] screen-share stop failed: {e}")
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

    # Stall instrumentation (2026-05-04 / 2026-05-05).
    #
    # Two complementary diagnostics for two different stall classes:
    #
    # (a) `slow_callback_duration = 0.5` — asyncio logs a WARNING with
    #     the offending callback/Task whenever it finishes after >0.5s.
    #     Catches *slow but finishing* coroutines.
    #
    # (b) `_dump_stall_diagnostics()` invoked by `_watchdog_thread`
    #     before `os._exit(1)` — dumps every Python thread stack via
    #     faulthandler + every asyncio task with its frame stack.
    #     Catches *fully wedged* loops where the callback never returns
    #     (blocking I/O, C-extension holding the GIL, sync-over-async).
    #     The 2026-05-05 stalls (3 in 24h: 09:56, 10:26, 11:15) emitted
    #     ZERO slow-callback warnings, proving (a) alone is not enough.
    #
    # Capturing the running loop into the watchdog lets the OS thread
    # ask asyncio for the task list at stall time.
    loop.slow_callback_duration = 0.5

    # Instantiate the watchdog now that `state` + `_restart_agent_unit`
    # are available. Class encapsulates all watchdog state so we no
    # longer need module-level globals for _last_heartbeat / _main_loop.
    global _watchdog
    _watchdog = LoopWatchdog(
        state=state,
        log=log,
        restart_agent_unit=_restart_agent_unit,
    )
    _watchdog.start_os_thread(loop)
    asyncio.create_task(_watchdog.heartbeat_loop(shutdown), name="heartbeat")
    asyncio.create_task(_watchdog.agent_presence_watchdog(shutdown), name="presence-watchdog")
    asyncio.create_task(_watchdog.stale_stt_watchdog(shutdown), name="stale-stt-watchdog")
    log.info(
        f"[watchdog] enabled — heartbeat every {WATCHDOG_HEARTBEAT_SEC}s, "
        f"kill if stale > {WATCHDOG_STALE_SEC}s"
    )

    # systemd sd_notify watchdog. Runs in the same asyncio loop as the
    # LiveKit + HTTP tasks — if the loop stalls, pings stop and systemd
    # kills + restarts us within WatchdogSec=10s (two missed pings).
    # Complements the OS-thread heartbeat above: that one handles a
    # fully-wedged loop (os._exit); this one tells systemd we're healthy
    # during normal operation (READY=1) and initiating a clean shutdown
    # (STOPPING=1).
    from resilience.watchdog import watchdog_loop
    asyncio.create_task(watchdog_loop(shutdown), name="sd-notify-watchdog")

    # Ensure the TTS provider file exists so the Tauri desktop can read
    # the current voice at startup without waiting for a user interaction.
    _ensure_tts_provider_file()

    # HTTP control plane runs for the whole process lifetime — survives
    # LiveKit reconnects so the Tauri UI gets a quick "connected=false"
    # during a blip rather than a 404. The handlers access _mic_pub_ref
    # and _room_ref via the lambdas below; the lambdas look the names
    # up at call time so they always see the live values across
    # reconnects (rather than the None they were when the api was
    # constructed).
    http_api = VoiceClientHttpApi(
        state=state,
        get_mic_pub=lambda: _mic_pub_ref,
        get_room=lambda: _room_ref,
        get_screen_share=lambda: _screen_share,
        restart_agent_unit=_restart_agent_unit,
        log=log,
    )
    http_runner = await http_api.start_server()

    # Supervisor loop — two-tier ReconnectLadder on transient errors so
    # a blip in the SFU (or a reboot) doesn't leave the client dead.
    # Tier 1: cheap resume() attempts with exponential backoff + jitter.
    # Tier 2: full teardown + reconnect after all resume slots exhaust.
    # After max_full_reconnects consecutive tier-2 failures, SystemExit
    # so systemd's Restart=always takes over with a clean process.
    from resilience.reconnect_ladder import ReconnectLadder

    async def _resume() -> bool:
        """Tier-1 resume: try a fresh run_once cycle. Returns True on
        clean exit, False on any disconnect/exception."""
        try:
            await run_once(shutdown)
            return True
        except Exception as e:
            log.warning(f"[resume] failed: {e}")
            return False

    async def _full_teardown() -> None:
        """Tier-2 'teardown' — the room was already disconnected by
        run_once's finally block before we got here. This is just a
        settle-time gap before the next resume cycle, giving the SFU
        a moment to clean up its side. Distinct from tier-1 (immediate
        retry) only by adding this 1s delay; if a future failure mode
        needs a real teardown step (delete + recreate room, fresh
        token mint, IPC reconnect), put it here."""
        await asyncio.sleep(1)

    ladder = ReconnectLadder(
        resume_fn=_resume,
        full_teardown_fn=_full_teardown,
    )

    try:
        while not shutdown.is_set():
            try:
                await run_once(shutdown)
                # Clean return — was it shutdown, or a disconnect we should recover from?
                if shutdown.is_set():
                    break
                # Disconnect without process shutdown → reconnect via ladder.
                log.info("[supervisor] disconnect detected; entering reconnect ladder")
                await ladder.recover()
            except Exception as e:
                log.exception(f"[supervisor] run_once crashed: {e}")
                await ladder.recover()
    finally:
        shutdown.set()
        await http_runner.cleanup()
    log.info("bye")


if __name__ == "__main__":
    asyncio.run(main())
