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
from collections import deque
import faulthandler
import json
import logging
import os
import signal
import sys
import threading
import time
import traceback
from dataclasses import dataclass, asdict, field
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

# ── PortAudio device routing ─────────────────────────────────────────
# PortAudio on this venv is compiled with only the ALSA host API (no
# native libpulse backend). Without an explicit device name, sounddevice
# defaults to ALSA's first card and grabs `hw:1,0` EXCLUSIVELY at the
# kernel level — Zoom / Discord / browser then can't access the mic
# while voice-client is running. Routing via the `pulse` device (which
# is ALSA's `default` PCM plug-routed through pipewire-pulse) puts
# voice-client on PipeWire's normal graph, where any number of clients
# can share the same source. Defaults are env-overridable in case the
# user has a non-pipewire stack.
AUDIO_INPUT_DEVICE  = os.environ.get("JARVIS_AUDIO_INPUT_DEVICE",  "pulse")
AUDIO_OUTPUT_DEVICE = os.environ.get("JARVIS_AUDIO_OUTPUT_DEVICE", "pulse")

# ── WebRTC APM (noise suppression + AGC + HPF) ───────────────────────
# Chromium's WebRTC AudioProcessingModule cleans up the mic before it
# hits the SFU. NS removes background hiss/fans, AGC levels the speech
# volume so the STT model sees consistent loudness, HPF strips rumble.
# Echo-cancellation is intentionally OFF here — `play_subscribed_track`
# would need to feed every playback frame through `process_reverse_stream`
# to drive the AEC, which is a bigger change; we lean on PipeWire's
# system-level `module-echo-cancel` (or LiveKit's server-side AEC) for
# echo handling instead. Disable any of these via env var. Module-level
# so AGC state persists across reconnects.
_APM_NS  = os.environ.get("JARVIS_APM_NS",  "1") == "1"
_APM_AGC = os.environ.get("JARVIS_APM_AGC", "1") == "1"
_APM_HPF = os.environ.get("JARVIS_APM_HPF", "1") == "1"
_APM_AEC = os.environ.get("JARVIS_APM_AEC", "0") == "1"
_apm: Optional["rtc.apm.AudioProcessingModule"] = None
if _APM_NS or _APM_AGC or _APM_HPF or _APM_AEC:
    from livekit.rtc import apm as _lk_apm
    _apm = _lk_apm.AudioProcessingModule(
        echo_cancellation=_APM_AEC,
        noise_suppression=_APM_NS,
        high_pass_filter=_APM_HPF,
        auto_gain_control=_APM_AGC,
    )

# ── L2 reverse-stream + barge-in (echo-cancellation cascade, 2026-05-19) ─
# The APM AEC (and the later DTLN residual) need the playback reference
# fed through `process_reverse_stream` plus an accurate stream-delay
# estimate. The estimator tracks output(DAC)/input(ADC) timestamps; the
# ring buffer holds the 16 kHz reference DTLN consumes. Both module-level
# so they survive LiveKit reconnects (run_once is re-entered per drop).
# Spec: docs/superpowers/specs/2026-05-19-echo-cancellation-cascade-design.md §5.2
from audio.apm_reverse_stream import APMDelayEstimator, ReverseRefRingBuffer
_reverse_estimator = APMDelayEstimator()
_reverse_ringbuf = ReverseRefRingBuffer(capacity_frames=64)

# ── Viseme lip-sync engine (kiosk talking face) ──
# Module-level singleton; survives LiveKit reconnects. Fed the agent's TTS
# transcript via set_pending_text() and ticked per playback frame via frame().
from lipsync import VisemeEngine
_viseme_engine = VisemeEngine()

# ── L3 DTLN neural residual filter (2026-05-22, Phase B Task 10 wiring) ──
# Module-level lazy singleton. The first call to `_get_dtln()` from the
# mic callback triggers the load (TFLite interpreters + SHA verification,
# ~50-100 ms). Subsequent calls return the cached instance. A failed load
# latches a sentinel so we don't retry-thrash on every mic frame.
#
# Operator ceiling: `JARVIS_NEURAL_AEC=0` disables the layer entirely
# (returns None permanently). Default: enabled.
#
# Phase B (THIS COMMIT) does NOT promote the mic-gate — `_HOT_MIC_SET`
# stays "none" in `aec_health.py`; mic still drops during TTS until the
# soak validates a set. This commit only makes `dtln.healthy` become
# True at runtime so telemetry reflects it and the gate is ready to flip.
# Spec: docs/superpowers/specs/2026-05-20-aec-cascade-completion-design.md §5.2
_dtln: Optional["DTLNResidualFilter"] = None
_dtln_load_attempted: bool = False  # sentinel — load only once per process


def _get_dtln() -> Optional["DTLNResidualFilter"]:
    """Lazy module-level accessor for the DTLN residual filter.

    Returns the loaded `DTLNResidualFilter` singleton, or None if disabled
    via `JARVIS_NEURAL_AEC=0` (operator ceiling) or if the load failed at
    any point in this process (failure is sticky — we don't retry).

    Safe to call from the realtime mic callback: after the first successful
    call, this is a single module-level attribute read.
    """
    global _dtln, _dtln_load_attempted
    if os.environ.get("JARVIS_NEURAL_AEC", "1") == "0":
        return None
    if _dtln is not None:
        return _dtln
    if _dtln_load_attempted:
        return None  # previous load failed; don't retry
    _dtln_load_attempted = True
    try:
        from audio.dtln_aec import DTLNResidualFilter, LATENCY_BUDGET_MS_DEFAULT
        _dtln = DTLNResidualFilter()
        # Cheap log of the live budget so operators see the resolved value.
        budget = float(os.environ.get(
            "JARVIS_NEURAL_AEC_LATENCY_BUDGET_MS",
            str(LATENCY_BUDGET_MS_DEFAULT),
        ))
        log.info(f"[dtln] loaded, model=128, budget={budget:.1f}ms")
        return _dtln
    except Exception as e:
        log.warning(f"[dtln] disabled — load failed: {type(e).__name__}: {e}")
        _dtln = None
        return None


def _apply_dtln_to_mic(
    frame_48k_int16_bytes: bytes,
    adc_t: float,
    ring,
    profile: str,
    dtln,
) -> bytes:
    """Apply DTLN L3 residual cancellation to a single 48 kHz int16 mic
    frame; return cleaned bytes (or the input bytes unchanged on any
    condition that means "skip cleaning").

    Pure helper — no module-level state mutation, no logging side-effects
    beyond an exception case. Factored out of `_mic_cb` so the integration
    test can drive it directly without standing up sounddevice/APM/asyncio.

    Skip conditions (return input unchanged):
      - dtln is None or not healthy
      - profile != "speakers" (headphones have no echo path)
      - mic/ref shape mismatch after downsample (no ref playback)
      - any exception in the inference path (fail-safe — realtime path
        must never raise)
    """
    if dtln is None or not dtln.healthy or profile != "speakers":
        return frame_48k_int16_bytes
    try:
        from scipy.signal import resample_poly as _rp
        mic48 = np.frombuffer(frame_48k_int16_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        mic16 = _rp(mic48, up=1, down=3).astype(np.float32)
        ref16 = ring.read_16k_aligned(adc_t)
        if mic16.shape != ref16.shape:
            return frame_48k_int16_bytes
        cleaned16 = dtln.process(mic16, ref16)
        cleaned48 = _rp(cleaned16, up=3, down=1).astype(np.float32)
        pcm48 = np.clip(cleaned48 * 32768.0, -32768, 32767).astype(np.int16)
        return pcm48.tobytes()
    except Exception:
        return frame_48k_int16_bytes


# Current output-device profile ("headphones"/"speakers"/"unknown"),
# updated event-driven by the watch_for_changes callback in main()
# (NOT polled per mic frame). _mic_cb reads this on the realtime
# callback path instead of calling classify_output_device() every
# speaking-frame — that removes the only non-trivial work from the
# 10 ms-deadline hot path AND fixes the 30 s lru-cache hot-plug
# staleness (a headphones↔speakers flip is reflected immediately via
# pw-mon instead of after the next TTL expiry). Spec §5.5 (T7 review #4).
_current_profile: str = "unknown"


def _should_publish_during_speak(*, profile: str, defense) -> bool:
    """2026-05-20 — keep the mic hot during TTS ONLY when the soak-validated
    -SUFFICIENT echo-defense set is MEASURED active (not env flags, not 'any
    layer'). L1-alone is present yet insufficient — that's the regression.
    See audio.aec_health.sufficient_for_hot_mic + the 2026-05-20 spec."""
    from audio.aec_health import sufficient_for_hot_mic
    return sufficient_for_hot_mic(defense, profile)

# ── Local listening-indicator detection ───────────────────────────────
# We compute mic frame RMS in `_mic_cb` and flip `state.listening`
# directly instead of relying on the SFU's `active_speakers_changed`
# event — the event was unreliable in production (never fired on a
# 2026-05-15 Latitude 7480 / LiveKit 1.5.9 setup), leaving the tray
# stuck on `idle` (green) regardless of who was talking.
#
# `state.speaking` is driven equally locally in `play_subscribed_track`:
# we set it true while we're actively rendering a remote audio track,
# false when the stream ends. Both halves of the indicator are now
# local-only signals; no SFU round-trip needed.
#
# ARCHITECTURAL TRADE-OFF (global review §P0-20, 2026-05-16):
# The listening flag is **RMS-driven, not VAD-driven**. The voice-agent
# runs Silero VAD in a separate process and its activation/deactivation
# is the ground truth for "is the user speaking right now"; the
# voice-client doesn't have access to that signal without an IPC hop.
# Result: ambient noise > LISTENING_RMS_THRESHOLD trips the indicator
# even when Silero correctly rejects it as non-speech, leading to
# "stuck cyan" complaints. The Q2 fix is to publish Silero's user-state
# from the agent over a LiveKit data channel and subscribe here.
# Until then, the threshold needs to clear typical room-tone RMS for
# the user's environment — bumped to 28k for the Latitude 7480 / Intel
# HDA pipeline 2026-05-16.
#
# RMS threshold tuned for 16-bit PCM AGC-normalized speech (~2000-10000
# during speech, ~50-500 in silence in a quiet room; up to ~25000 in
# noisy environments with mic gain).
_LISTENING_RMS_THRESHOLD = float(os.environ.get("JARVIS_LISTENING_RMS_THRESHOLD", "1500"))
# Once tripped, hold listening=True for this many seconds after the
# last above-threshold frame. Stops the indicator from flickering
# between syllables and ignores brief gaps in normal speech cadence.
_LISTENING_HOLD_S = float(os.environ.get("JARVIS_LISTENING_HOLD_S", "0.6"))

# Same idea on the playback side: the LiveKit agent's audio track is
# opened the moment the agent joins and stays open for the whole
# session (silence frames flow continuously). If we set state.speaking
# = True for the entire stream's lifetime, the indicator would be
# pinned blue from the moment the agent joins, regardless of whether
# JARVIS is actually voicing anything. RMS-gate it the same way as
# listening. Hold is generous (1.2 s) because EdgeTTS / Orpheus emit
# noticeable inter-phrase silence — a tighter hold makes the indicator
# flap between every comma, which then unmasks the mic for half a
# second and bounces the listening indicator on speaker→mic echo.
_SPEAKING_RMS_THRESHOLD = float(os.environ.get("JARVIS_SPEAKING_RMS_THRESHOLD", "800"))
_SPEAKING_HOLD_S = float(os.environ.get("JARVIS_SPEAKING_HOLD_S", "1.2"))

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
    # Routed through pipeline.service_control so Linux behavior is
    # preserved (systemctl --user restart) and Windows can swap in a
    # real nssm/sc.exe backend in Phase 3 without touching this call
    # site. The agent-then-client ordering + 4 s gap is the same as
    # before; we just no longer get the agent restart's stderr (the
    # helper silences both streams). systemd's journal still has it.
    from pipeline.service_control import (
        restart_service_async,
        ServiceControlError,
    )
    try:
        rc = await restart_service_async("jarvis-voice-agent")
        if rc not in (None, 0):
            log.warning(f"agent restart returned {rc}")
            return
        log.info("agent unit restart kicked, waiting before bouncing self")
    except ServiceControlError as e:
        log.warning(f"could not restart agent — service control unavailable: {e}")
        return
    except Exception as e:
        log.warning(f"could not restart agent: {e}")
        return

    # 4 s is enough on this host for Silero VAD prewarm + worker
    # registration; tune up if the agent log shows "registered worker"
    # arrives later.
    await asyncio.sleep(4)

    try:
        await restart_service_async("jarvis-voice-client")
    except ServiceControlError as e:
        log.warning(f"could not restart self — service control unavailable: {e}")
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
    output_level:  float = 0.0        # 0..1 RMS of played TTS — drives kiosk face lip-sync
    # Current frame's ARKit-morph weights {target_N: 0..1} for the kiosk
    # face's visemes. Updated by the playback loop via the VisemeEngine;
    # published on GET /face. Empty dict = mouth at rest.
    face_weights:  dict = field(default_factory=dict)
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
# Set inside main() right after construction; lets the data_received
# handler (registered in run_once, a different function scope) forward
# assistant_says packets to SSE subscribers without threading the
# reference through every helper. None until main() runs.
_http_api_ref: Optional["VoiceClientHttpApi"] = None

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
        # 10 ms blocksize is right for tight A/V sync, but PortAudio's
        # pulse plugin pipeline + scheduling jitter make `latency="low"`
        # too tight on this stack — live failure 2026-05-15 logged 183
        # `ALSA underrun occurred` in 500 lines. Each underrun = a brief
        # gap or zero-fill in playback, which is exactly the chopped/
        # robotic timbre the user reported. 200 ms gives ~20 frames of
        # ring-buffer headroom — invisible to human ears for conversation
        # (Zoom et al. run with 100–300 ms output latency) and plenty
        # for the scheduler + LiveKit RTC AudioStream jitter combined.
        # Earlier attempt with 80 ms still produced ~7 underruns/sec on
        # the second sequential /speak; 200 ms held clean.
        blocksize=FRAME_SAMPLES,
        latency=float(os.environ.get("JARVIS_PLAYBACK_LATENCY_S", "0.2")),
        # `pulse` here is ALSA's `default` PCM auto-routed through
        # pipewire-pulse (see module-level AUDIO_OUTPUT_DEVICE comment).
        # Without this, PortAudio grabs hw:X,Y directly and locks the
        # speaker against other apps. Env-overridable.
        device=AUDIO_OUTPUT_DEVICE,
    )
    out.start()
    log.info(f"[playback] OPEN track={track.sid} sr={SAMPLE_RATE}Hz ch={NUM_CHANNELS} device={AUDIO_OUTPUT_DEVICE!r}")
    # `state.speaking` drives the tray's "talking" (blue) colour. The
    # remote track stays open the whole session (silence frames flow
    # continuously between TTS utterances). 2026-05-20: drive it from
    # the OUTGOING TTS PCM (clean, known signal) instead of mic-side
    # RMS, so the mic-drop fallback never false-mutes the user on
    # ambient noise. Uses audio.speaking_signal.is_rendering_speech which
    # threshold-gates on the outgoing int16 RMS at 300 (well below Orpheus
    # speech; well above the always-open silent track). Hold is still
    # _SPEAKING_HOLD_S (1.2 s default) so the indicator doesn't flap
    # between inter-phrase silences. Spec 2026-05-20 §5.5.
    _speaking_until = [0.0]  # mutable closure cell; one-element list
    try:
        async for event in stream:
            frame = event.frame
            # frame.data is a bytes-like buffer of int16 LE samples.
            # Reshape (samples, channels) for sounddevice.
            pcm = np.frombuffer(frame.data, dtype=np.int16).reshape(-1, NUM_CHANNELS)
            # 2026-05-19 — feed APM the playback reference + stash for
            # DTLN. Without this, APM AEC has no reference. Spec §5.2/§6.2.
            #
            # DAC timestamp on the PortAudio stream clock (Pa_GetStreamTime),
            # NOT time.monotonic() — the mic side reads inputBufferAdcTime
            # off the SAME clock, so differencing them yields the acoustic
            # round-trip. time.monotonic() is a different clock dominated by
            # asyncio jitter + the 200 ms playback buffer (T7 review #1).
            # This is blocking-write mode (no per-frame time_info), so we
            # use the stream's running clock + output latency as the
            # approximate playout time of this frame; JARVIS_APM_DELAY_BIAS_MS
            # tunes any residual offset that approximation leaves.
            try:
                dac_t = out.time + out.latency   # approx playout time of frame
            except Exception:
                dac_t = time.monotonic()         # fallback if stream clock unavailable
            try:
                # process_reverse_stream is an AEC-only APM method — only
                # valid when echo processing is enabled (T7 review #2).
                if _apm is not None and _APM_AEC:
                    _apm.process_reverse_stream(frame)
                # The estimator + ring buffer stay UNGATED: the ring feeds
                # L3/DTLN which runs regardless of APM AEC, and the same
                # dac_t keeps the L3 reference alignment consistent with
                # the estimator.
                _reverse_estimator.note_output(dac_t)
                _reverse_ringbuf.write(
                    np.frombuffer(frame.data, dtype=np.int16).astype(np.float32) / 32768.0,
                    dac_ts=dac_t,
                )
            except Exception as e:
                log.debug(f"[playback] reverse-stream feed failed: {e}")
            # Drive state.speaking from the outgoing TTS PCM (clean,
            # known signal) — not mic-side RMS which can false-positive
            # on ambient noise. Spec 2026-05-20 §5.5.
            from audio.speaking_signal import is_rendering_speech
            if is_rendering_speech(np.frombuffer(frame.data, dtype=np.int16)):
                state.speaking = True
                _speaking_until[0] = time.monotonic() + _SPEAKING_HOLD_S
            elif time.monotonic() > _speaking_until[0]:
                state.speaking = False
            # Output amplitude (0..1, normalized RMS) of the played TTS frame —
            # the kiosk WebGL face polls /level and drives the jaw morph from
            # this. Lightly smoothed; cheap (one np.sqrt per 10ms frame).
            _lvl = float(np.sqrt(np.mean(pcm.astype(np.float32) ** 2))) / 32768.0
            state.output_level += (_lvl - state.output_level) * 0.5
            # Viseme lip-sync: resolve this frame's ARKit-morph weights from
            # the agent's known text + the smoothed RMS. Never raises into
            # the audio path — on any error the face just falls back to rest.
            try:
                state.face_weights = _viseme_engine.frame(
                    now=time.monotonic(),
                    speaking=state.speaking,
                    rms=state.output_level,
                )
            except Exception as e:
                log.debug(f"[lipsync] frame failed: {e}")
                state.face_weights = {}
            # write() is non-blocking-ish — it copies into PortAudio's
            # internal ring, the audio thread drains. If we ever fall
            # behind, it returns a buffer-underflow warning; harmless
            # enough for a conversational pace.
            out.write(pcm)
    except Exception as e:
        log.warning(f"[playback] stream error: {e}")
    finally:
        state.speaking = False
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
        # The SFU's active_speakers_changed event was originally the
        # authoritative source for the listening/speaking indicators,
        # but on LiveKit 1.5.9 it never fired on the local 7480 setup
        # (2026-05-15) — the tray stayed `idle` no matter who was
        # talking. We now drive `state.listening` from mic-side RMS in
        # `_mic_cb` and `state.speaking` from `play_subscribed_track`'s
        # render loop, both local signals. This handler is kept only
        # for the watchdog liveness ping, which is cheap and harmless
        # to leave wired even when the event isn't firing reliably.
        local_active = any(p.identity == IDENTITY for p in speakers)
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
        # Republish mic on EVERY agent participant_connected — gives
        # the new LiveKit Job a fresh track SID to subscribe to,
        # avoiding the zombie-subscription bug observed 2026-05-17/18
        # (3 back-to-back reconnect cycles each landed with no STT
        # events firing for ~50min). Each agent Job has a unique
        # identity (`agent-AJ_*`), so a new participant_connected
        # event always implies a new Job. Cost: one ~50ms SID swap
        # per connect (negligible — audio frames keep flowing through
        # the same AudioSource). Opt-out: JARVIS_REPUBLISH_ON_AGENT_REJOIN=0.
        if not participant.identity.startswith("agent-"):
            return
        if os.environ.get("JARVIS_REPUBLISH_ON_AGENT_REJOIN", "1") != "1":
            return
        asyncio.create_task(_republish_mic_track())

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

    @room.on("data_received")
    def _on_data_received(packet: rtc.DataPacket) -> None:
        """Forward assistant_says packets from the agent participant to
        the SSE subscribers (the tray chat panel).

        LiveKit's data_received fires only for packets from REMOTE
        participants — self-published data (the /user-input + /speak
        + /stop emits this voice-client makes) does not loop back here,
        so the remote-only side is already handled by the SDK.

        On top of that we identity-filter by the `agent-` prefix (the
        same convention `_on_participant_connected` above relies on —
        LiveKit's worker assigns `agent-AJ_*` identities). Today only
        the voice agent publishes `{type:assistant_says}`, so this is
        defense against future multi-publisher rooms where another
        remote client might emit the same shape.
        """
        try:
            identity = packet.participant.identity if packet.participant else ""
        except Exception:
            identity = ""
        if not identity.startswith("agent-"):
            return
        try:
            msg = json.loads(packet.data.decode("utf-8"))
        except Exception:
            return
        if not isinstance(msg, dict):
            return
        if msg.get("type") != "assistant_says":
            return
        text = (msg.get("text") or "").strip()
        if not text:
            return
        # _http_api_ref is the module-level singleton set in main() right
        # after VoiceClientHttpApi construction. It's None until main()
        # has constructed the api; in practice the room can't fire
        # data_received before run_once is invoked from main()'s ladder,
        # but we guard defensively anyway so a corner-case race can't
        # crash the room loop.
        api = _http_api_ref
        if api is None:
            return
        try:
            api.enqueue_event({
                "type": "assistant_says",
                "text": text,
                "ts_ms": msg.get("ts_ms"),
            })
        except Exception as e:
            log.debug(f"[chat-panel] enqueue_event failed: {e!r}")

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
            buf = []
            async for chunk in reader:
                buf.append(chunk)
            text = "".join(buf).strip()
            # Only the agent's TTS transcript drives the face, not our own
            # STT echoed back under the local identity (IDENTITY).
            if text and participant_identity != IDENTITY:
                _viseme_engine.set_pending_text(text)
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
    # Pin the AudioSource to THIS run_once loop. The mic pump is now the sole
    # caller of capture_frame; binding the source's future-loop here avoids a
    # silent off-loop wedge (every frame would drop, looking like backpressure).
    source = rtc.AudioSource(SAMPLE_RATE, NUM_CHANNELS, loop=loop)
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

    # `_republish_mic_track` — invoked from `_on_participant_connected`
    # when the agent re-joins. Gives the new LiveKit Job a fresh track
    # SID to subscribe to, sidestepping the zombie-subscription bug
    # observed 2026-05-17 (3 reconnect cycles each landed with no STT
    # events ever firing). Reuses the same AudioSource so the mic
    # callback's `source.capture_frame(...)` keeps flowing — only the
    # LocalAudioTrack + LocalTrackPublication get swapped. The new
    # publish completes BEFORE the old unpublish so audio doesn't drop
    # to silence mid-rotation.
    nonlocal_mic = {"track": mic_track, "pub": mic_pub}
    async def _republish_mic_track() -> None:
        global _mic_pub_ref  # module-level global the HTTP mute handler reads
        old_pub = nonlocal_mic["pub"]
        old_track = nonlocal_mic["track"]
        try:
            new_track = rtc.LocalAudioTrack.create_audio_track("mic", source)
            new_pub = await room.local_participant.publish_track(
                new_track,
                rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE),
            )
            nonlocal_mic["track"] = new_track
            nonlocal_mic["pub"] = new_pub
            _mic_pub_ref = new_pub
            log.info(
                f"[mic] republished — new sid={new_pub.sid} "
                f"(was {old_pub.sid})"
            )
        except Exception as e:
            log.warning(f"[mic] republish FAILED: {type(e).__name__}: {e}")
            return  # keep old pub in place
        # Unpublish old AFTER the new one is live — minimizes audio gap.
        try:
            await room.local_participant.unpublish_track(old_pub.sid)
            log.info(f"[mic] old sid={old_pub.sid} unpublished")
        except Exception as e:
            log.warning(
                f"[mic] old unpublish failed (harmless if remote already "
                f"dropped it): {type(e).__name__}: {e}"
            )
        # old_track is implicitly dropped; the SFU has already replaced
        # the subscription on the agent side.

    # ── Mic → SFU bridge: FULLY DECOUPLED from the event loop ──────────
    # The PortAudio callback runs on a realtime thread ~100×/s and MUST NOT
    # schedule per-frame work on the asyncio loop: source.capture_frame
    # back-pressures whenever the SFU isn't draining the mic (muted in a
    # direct mode, or no agent consumer), and ANY per-frame loop scheduling
    # (run_coroutine_threadsafe OR call_soon_threadsafe) then floods the loop
    # → it can't serve the :8767 control endpoint (mode-switch mute hangs)
    # or ping the systemd watchdog (SIGABRT). Root-cause fix (2026-05-29):
    # the callback only appends to a bounded, thread-safe deque (append/len/
    # popleft are atomic under the GIL; maxlen auto-drops the OLDEST frame
    # when full — inaudible, and correct under backpressure). One pump task
    # drains it and is the SOLE caller of capture_frame (≤1 in-flight). The
    # loop never does per-frame work, so backpressure cannot saturate it.
    _MIC_RING_MAX = 50  # 50 × 10ms ≈ 500ms jitter buffer before dropping oldest
    _mic_ring: "deque[rtc.AudioFrame]" = deque(maxlen=_MIC_RING_MAX)
    _mic_drops = [0]

    async def _mic_pump() -> None:
        # Sole consumer of source.capture_frame. Under backpressure it blocks
        # here, the ring fills, and the realtime callback's maxlen append
        # drops the oldest — bounded, with ZERO loop involvement. The short
        # sleep runs only when the ring is momentarily empty (frames flow
        # continuously, so that's rare). Drop count surfaced here, off the
        # 10ms realtime hot path. Uses the stable `source` (same AudioSource
        # survives mic republish — see _republish_mic_track).
        pumped = 0
        last_drops = 0
        while True:
            if not _mic_ring:
                await asyncio.sleep(0.005)
                continue
            try:
                frame = _mic_ring.popleft()
            except IndexError:
                continue
            pumped += 1
            if pumped % 500 == 0 and _mic_drops[0] != last_drops:
                log.warning(
                    f"[mic] dropped {_mic_drops[0]} frame(s) total under SFU "
                    f"backpressure (ring bounded at {_MIC_RING_MAX})"
                )
                last_drops = _mic_drops[0]
            try:
                await source.capture_frame(frame)
            except Exception as e:
                log.debug(f"[mic] capture_frame failed (harmless mid-rotation): {e}")

    #
    # If WebRTC APM is enabled (`_apm` non-None), the frame is passed
    # through `process_stream` for NS + AGC + HPF (+ optional AEC) BEFORE
    # publication. APM mutates the frame buffer in place; it requires
    # exactly 10 ms of audio, which is what FRAME_MS gives us.
    #
    # CRITICAL: we compute RMS for `state.listening` on the *RAW* input
    # BEFORE APM. AGC is on by default and aggressively amplifies quiet
    # ambient noise to normalize speech loudness — that gave a post-APM
    # RMS floor well above the threshold even in a silent room, so the
    # indicator pinned to `listening=True` forever (live failure
    # 2026-05-15: 4 polls/sec all `listening=True` with nobody talking,
    # tray stuck on cyan). Raw RMS is whatever the mic actually heard.
    # Skipped while `state.speaking` is true so speaker→mic echo
    # (we leave AEC off in voice-client; PipeWire's module-echo-cancel
    # handles it system-wide) doesn't flap the indicator into "you're
    # talking!" every time JARVIS is the one talking.
    _listening_until = [0.0]  # mutable closure cell; one-element list
    def _mic_cb(indata, frames, _time, status) -> None:
        if status:
            log.debug(f"[mic] portaudio status: {status}")
        # Muted (e.g. a direct mode muted JARVIS-Claude) → send NOTHING.
        # Capturing into a muted, undrained LiveKit track back-pressures the
        # AudioSource and floods the event loop with per-frame work, which
        # starves the HTTP control server (:8767 /mute,/status) + the watchdog
        # heartbeat — making the NEXT mode-switch's mute call hang ~30s (the
        # recurring 2026-05-29 "can't switch modes" trap). Muted means we
        # shouldn't be publishing mic audio anyway, so drop here at the source.
        if state.muted:
            state.listening = False  # don't latch the tray indicator through a mute
            return
        # RMS on raw audio (pre-APM, pre-AGC) for the listening detector.
        if not state.speaking and len(indata):
            raw_rms = float(np.sqrt(np.mean(indata.astype(np.float32) ** 2)))
        else:
            raw_rms = 0.0
        frame = rtc.AudioFrame(
            data=indata.tobytes(),
            sample_rate=SAMPLE_RATE,
            num_channels=NUM_CHANNELS,
            samples_per_channel=frames,
        )
        if _apm is not None and _APM_AEC:
            # 2026-05-19 — set the round-trip stream delay (from the
            # reverse-stream estimator) BEFORE process_stream so the APM
            # AEC aligns the reference to the mic. Spec §5.2.
            #
            # set_stream_delay_ms is an AEC-only APM method, so the whole
            # block is gated on _APM_AEC, not just `_apm is not None`
            # (T7 review #2) — no point estimating the delay when its only
            # consumer is off. The ADC timestamp comes from PortAudio's
            # time_info (inputBufferAdcTime = when this mic buffer was
            # captured on Pa_GetStreamTime), the SAME clock the playback
            # DAC time uses — NOT time.monotonic() (T7 review #1).
            try:
                adc_t = _time.inputBufferAdcTime
            except Exception:
                adc_t = time.monotonic()
            try:
                _reverse_estimator.note_input(adc_t)
                _apm.set_stream_delay_ms(_reverse_estimator.current_delay_ms())
            except Exception as e:
                log.debug(f"[mic] delay-estimate failed: {e}")
        if _apm is not None:
            try:
                _apm.process_stream(frame)
            except Exception as e:
                log.warning(f"[mic] APM process_stream failed: {e}")
        # ── L3 (DTLN neural residual) ────────────────────────────────────
        # Run AFTER L2 APM (NS/HPF/AGC + optional AEC), so DTLN sees the
        # cleanest residual the linear layers leave. Speakers-only —
        # headphones have no echo path, L3 would just burn CPU.
        # Fail-safe: any exception drops to passthrough (mic frame unchanged).
        # Spec §5.2/§6.2.
        #
        # Publish format: we keep the existing 48 kHz publish path (option
        # 2a per the spec) — DTLN runs at 16 kHz, so the helper downsamples
        # in / upsamples out and re-wraps into an AudioFrame. Switching to
        # 16 kHz publish (option 2b) is deferred — the gate still flips to
        # "true" with DTLN active via this path. Spec §5.2 / Task 10 Step 4.
        _dtln_ref = _get_dtln()
        if _dtln_ref is not None and _dtln_ref.healthy and _current_profile == "speakers":
            try:
                _adc_t = _time.inputBufferAdcTime
            except Exception:
                _adc_t = time.monotonic()
            _orig_bytes = frame.data
            _cleaned_bytes = _apply_dtln_to_mic(
                _orig_bytes, _adc_t, _reverse_ringbuf, _current_profile, _dtln_ref,
            )
            if _cleaned_bytes is not _orig_bytes:
                frame = rtc.AudioFrame(
                    data=_cleaned_bytes,
                    sample_rate=SAMPLE_RATE,
                    num_channels=NUM_CHANNELS,
                    samples_per_channel=len(_cleaned_bytes) // 2,  # int16 → 2 bytes/sample
                )
        if state.speaking:
            # Agent is rendering audio — force the indicator off so it
            # can transition cleanly into "talking" (blue). Without this
            # clear, any latched listening=True from before /speak
            # started stays true for the entire playback (no
            # timeout-clear runs because the whole block is gated).
            state.listening = False
        else:
            now = time.monotonic()
            if raw_rms > _LISTENING_RMS_THRESHOLD:
                state.listening = True
                _listening_until[0] = now + _LISTENING_HOLD_S
                if _watchdog is not None:
                    _watchdog.mark_voice_active()
            elif now > _listening_until[0]:
                state.listening = False
        # Speak-time mic gating (rewired 2026-05-19, echo-cancellation
        # cascade L2). The 2026-05-16 fix DROPPED every mic frame while
        # the agent spoke — it killed the speaker→mic echo loop but also
        # killed barge-in (user couldn't interrupt mid-reply). Now that
        # the APM AEC + the reverse-stream reference (above) + the neural
        # residual (L3) cancel JARVIS's own voice, we publish during TTS
        # whenever SOME echo defense is active; the blanket mic-drop
        # survives only as the legacy safety net (speakers, all AEC off).
        # `JARVIS_MIC_DURING_SPEAK=1` still forces publish (legacy opt-out
        # for headphone users — superseded by the profile detection below,
        # but honored so callers that set it don't regress). Spec §4.2/§6.2.
        if state.speaking and os.environ.get("JARVIS_MIC_DURING_SPEAK", "0") != "1":
            from audio.aec_health import current_echo_defense
            _gate_dtln = _get_dtln()
            _defense = current_echo_defense(
                apm_aec=(_apm is not None and _APM_AEC),
                dtln_healthy=(_gate_dtln is not None and _gate_dtln.healthy),
            )
            if not _should_publish_during_speak(profile=_current_profile, defense=_defense):
                return
        # Hand the frame to the pump via the bounded, thread-safe deque —
        # NO loop scheduling at all (no call_soon / run_coroutine_threadsafe),
        # which is what keeps the event loop free under backpressure so the
        # :8767 control endpoint + the watchdog heartbeat stay alive. append
        # and len are atomic under the GIL; maxlen drops the oldest frame if
        # the pump is behind. Count drops for visibility (the pump logs them,
        # off this realtime hot path).
        if len(_mic_ring) >= _MIC_RING_MAX:
            _mic_drops[0] += 1
        _mic_ring.append(frame)

    mic_stream = sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=NUM_CHANNELS,
        dtype="int16",
        blocksize=FRAME_SAMPLES,
        callback=_mic_cb,
        latency="low",
        # See module-level AUDIO_INPUT_DEVICE comment — routes through
        # pipewire-pulse so other apps can share the mic concurrently.
        device=AUDIO_INPUT_DEVICE,
    )
    # Start the single mic-pump consumer BEFORE the stream so no callback
    # frame is appended to the ring before a consumer exists. Bounds in-flight
    # capture_frame to 1 so SFU backpressure can never pile up tasks/RAM and
    # — with the fully-decoupled deque bridge — never saturates the loop
    # (2026-05-29/30 :8767 + watchdog root cause).
    mic_pump_task = asyncio.create_task(_mic_pump(), name="mic-pump")
    mic_stream.start()
    log.info(
        f"[mic] capture started device={AUDIO_INPUT_DEVICE!r} "
        f"apm={'on' if _apm else 'off'} "
        f"(ns={_APM_NS} agc={_APM_AGC} hpf={_APM_HPF} aec={_APM_AEC})"
    )

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
        mic_pump_task.cancel()
        # Await the cancellation so no frame is mid-capture_frame when the
        # room/source go away (this teardown runs on every reconnect blip).
        try:
            await mic_pump_task
        except asyncio.CancelledError:
            pass
        mic_stream.close()
        await room.disconnect()


async def main() -> None:
    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    # asyncio.add_signal_handler raises NotImplementedError on Windows
    # — both SIGTERM and SIGINT are POSIX concepts that the Windows
    # IOCP event loop can't route to a coroutine. On Linux this is the
    # canonical clean-shutdown path; on Windows we fall back to letting
    # the default KeyboardInterrupt/process termination propagate
    # naturally (Phase 3 will wire a proper Windows shutdown path via
    # SetConsoleCtrlHandler or sys.exit-on-CTRL_C_EVENT).
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, shutdown.set)  # windows-footgun: ok (wrapped in try/except NotImplementedError just below)
        except NotImplementedError:
            log.debug(
                "add_signal_handler(%s) unsupported on this platform "
                "(Windows asyncio doesn't route POSIX signals); "
                "falling back to default termination",
                sig,
            )

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

    # ── AEC state bridge + output-profile hot-plug watcher (2026-05-19) ─
    # The voice-client owns the AEC layers; the agent process reads this
    # JSON at turn-write time to stamp which echo defenses were active
    # (and the measured APM delay). Write once at startup, re-write on
    # every output-device transition (headphones↔speakers flips L3 on/off
    # and changes whether barge-in suppresses mic), and refresh
    # periodically so apm_delay_ms_p50 tracks the live estimate. Spec §5.5.
    from audio.output_profile import classify_output_device, watch_for_changes
    from audio.aec_state import write_aec_state
    from audio import aec_health as _aec_health

    def _write_aec_state_snapshot() -> None:
        prof = classify_output_device()
        # L3 (DTLN neural residual) — telemetry reflects runtime reality:
        # only `True` when the singleton is loaded AND healthy (latency under
        # budget + no inference exceptions). When None/unhealthy, both
        # `l3_active` and `dtln_latency_ms_p95` are off/None so the soak
        # rollup (bin/jarvis-aec-soak) sees the truthful activation rate.
        _snap_dtln = _get_dtln()
        _l3_active = bool(_snap_dtln is not None and _snap_dtln.healthy)
        _dtln_p95 = _snap_dtln.p95_ms if _l3_active else None
        write_aec_state(
            output_profile=prof,
            l1_active=_aec_health.l1_echo_cancel_active(),
            l2_aec_active=_APM_AEC,
            l3_active=_l3_active,
            apm_delay_ms_p50=_reverse_estimator.current_delay_ms(),
            dtln_latency_ms_p95=_dtln_p95,
        )

    # Seed + maintain the module-level `_current_profile` that _mic_cb
    # reads on the realtime path (T7 review #4). The watcher fires on
    # every headphones↔speakers transition (pw-mon, event-driven), so
    # the mic gate sees hot-plugs immediately instead of after the 30 s
    # lru-cache TTL — and the snapshot stays in sync with it.
    def _on_profile_change(prof: str) -> None:
        global _current_profile
        _current_profile = prof
        _write_aec_state_snapshot()

    global _current_profile
    _current_profile = classify_output_device()
    _write_aec_state_snapshot()
    watch_for_changes(_on_profile_change)

    # Periodic refresh so apm_delay_ms_p50 stays current between profile
    # transitions (the watcher only fires on device changes). 5 s cadence
    # matches the agent's 60 s staleness guard with wide margin. Consistent
    # with the create_task loops above — no new OS thread.
    async def _aec_state_refresh_loop() -> None:
        while not shutdown.is_set():
            try:
                await asyncio.sleep(5)
                _write_aec_state_snapshot()
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.debug(f"[aec_state] periodic refresh failed: {e}")
    asyncio.create_task(_aec_state_refresh_loop(), name="aec-state-refresh")

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
    # Expose http_api at module scope so the room.on("data_received")
    # handler in run_once() — a different function — can forward
    # assistant_says packets into SSE without having the api threaded
    # through its parameters. Same shape as _room_ref / _mic_pub_ref.
    global _http_api_ref
    _http_api_ref = http_api
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
