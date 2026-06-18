"""HTTP control plane for the voice client.

The Tauri UI (and any future client) polls / posts to this server to
read the voice-session snapshot + drive the mute / speak / stop /
model-switch surface. Kept on a distinct port from the bridge (8765)
and the speech sidecar (8766).

Encapsulated as `VoiceClientHttpApi` so the handlers can access the
shared `state` + the mutable `_mic_pub` / `_room` references without
relying on module-level globals. Pass current-value accessors
(`get_mic_pub`, `get_room`) at construction; the handlers call them
at request time so they always see the live values even as
`run_once` rebuilds the room across reconnects.

Routes:
  GET  /status        → snapshot of current state
  GET  /health        → same as /status (probed by systemd/launch.sh)
  POST /mute          → toggle local mic track mute
  POST /speak         → ask agent to voice text via TTS
  POST /stop          → interrupt current agent utterance
  POST /user-input    → inject synthetic user turn into AgentSession
  POST /screen-share  → start/stop X11 → LiveKit video publish
  GET  /cli-model     → current CLI model + allowlist
  POST /cli-model     → write CLI model choice
  GET  /voice-model   → current speech LLM + allowlist
  POST /voice-model   → write speech LLM choice + restart agent
  GET  /tts-provider  → current TTS provider + allowlist
  POST /tts-provider  → write TTS provider + restart agent
  OPTIONS /{any}      → CORS preflight

Hoisted from `jarvis_voice_client.py` 2026-05-10 (Step 7 of the
audit).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import time
from dataclasses import asdict
from typing import Any, Awaitable, Callable, Optional

from aiohttp import web

from voice_client_tray_config import (
    CLI_MODEL_FILE,
    CLI_MODELS_AVAILABLE,
    SPEECH_MODEL_FILE,
    SPEECH_MODELS_AVAILABLE,
    TTS_PROVIDER_FILE,
    TTS_PROVIDERS_AVAILABLE,
    TOOL_BUSY_FILE,
    SILENT_MODE_FILE,
    agent_is_thinking,
    read_cli_model,
    read_speech_model,
)


__all__ = ["STATUS_PORT", "VoiceClientHttpApi"]


# Distinct port from bridge (8765) / speech sidecar (8766).
STATUS_PORT: int = int(os.environ.get("JARVIS_VOICE_CLIENT_PORT", "8767"))


# CORS headers used on every response. Permissive on purpose — the
# Tauri webview polls us from `tauri://localhost` and the web app
# polls from the local dev origin; both need preflight-free access.
_CORS_HEADERS = {
    "Access-Control-Allow-Origin":  "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}

# Active conversation mode (bin/jarvis-mode writes it): jarvis|gemini|openai.
# In a direct mode the active voice is a separate process and JARVIS-Claude's
# mic is owned by the mode watchdog — see the /mute handler.
_ACTIVE_MODE_FILE = os.path.join(os.path.expanduser("~"), ".jarvis", "active-mode")


# Direct-mode units. A "direct mode" is only TRULY active when its backend
# process is alive — a stale active-mode file (the tool was killed/crashed
# without `jarvis-mode jarvis`) must NOT keep Claude's mic wedged.
_DIRECT_UNITS = {
    "gemini": "jarvis-gemini-tools.service",
    "openai": "jarvis-gpt-tools.service",
}
_DIRECT_LIVE_TTL_S = 2.0      # cache the systemctl probe (on the /mute + status paths)
_DIRECT_LIVE_GRACE_S = 20.0   # ride out a transient restart gap (GoAway → RestartSec=2)
_direct_live_cache: dict = {"mode": "", "ts": 0.0, "live": False, "last_live": 0.0}


def _active_mode() -> str:
    """Raw active-mode file value ('jarvis'|'gemini'|'openai'|'')."""
    try:
        with open(_ACTIVE_MODE_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def _write_active_mode(mode: str) -> None:
    """Atomically set the active-mode file. Used to reconcile a stale direct
    mode back to jarvis when the backend died without reverting — best-effort
    (the mic unmute is the part the user actually feels)."""
    try:
        path = _ACTIVE_MODE_FILE
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        tmp = f"{path}.tmp.{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(mode + "\n")
        os.replace(tmp, path)
    except Exception:
        pass


def _direct_unit_live(mode: str) -> bool:
    """True iff the direct-mode backend for `mode` is actually running.

    Cached for a couple seconds (this is on the hot /mute + /status paths) with
    a short GRACE window so a legitimate backend restart (Gemini/OpenAI send a
    GoAway every ~10-15min → clean exit → RestartSec=2 ~7s gap) doesn't read as
    dead and flip the mic mid-switch. Fail SAFE: if systemctl can't be probed,
    assume LIVE so a probe error never lets a user toggle steal the mic from a
    live direct voice.
    """
    unit = _DIRECT_UNITS.get(mode)
    if not unit:
        return False
    now = time.monotonic()
    c = _direct_live_cache
    if c["mode"] == mode and (now - c["ts"]) < _DIRECT_LIVE_TTL_S:
        live = c["live"]
    else:
        if shutil.which("systemctl") is None:
            return True
        try:
            rc = subprocess.run(
                ["systemctl", "--user", "is-active", "--quiet", unit],
                timeout=2,
            ).returncode
            live = rc == 0
        except Exception:
            return True
        c.update(mode=mode, ts=now, live=live)
        if live:
            c["last_live"] = now
    if live:
        return True
    return c["mode"] == mode and (now - c["last_live"]) < _DIRECT_LIVE_GRACE_S


def _direct_mode_active() -> bool:
    """A direct voice (Gemini/OpenAI) is the ACTIVE voice right now — the file
    says so AND its backend is alive (see _direct_unit_live)."""
    mode = _active_mode()
    return mode in ("gemini", "openai") and _direct_unit_live(mode)


class VoiceClientHttpApi:
    """Aiohttp control-plane server with explicit state injection.

    Construct once at startup with:
      - `state`: the shared ClientState snapshot the /status endpoint
        publishes.
      - `get_mic_pub`: returns the current `LocalTrackPublication` (or
        None when no room is connected). The /mute handler calls this
        at request time, so it sees the live publication even after a
        room reconnect.
      - `get_room`: returns the current `rtc.Room` (or None). The
        /speak, /stop, /user-input handlers use it to publish data
        packets.
      - `restart_agent_unit`: async callable invoked when /voice-model
        or /tts-provider changes the selection (the agent's LLM and
        TTS chain are built at session start; switching requires a
        restart).
      - `log`: logger to use for status / error messages.
    """

    def __init__(
        self,
        *,
        state: Any,
        get_mic_pub: Callable[[], Any],
        get_room: Callable[[], Any],
        get_screen_share: Optional[Callable[[], Any]] = None,
        restart_agent_unit: Callable[[], Awaitable[None]],
        log: logging.Logger,
    ) -> None:
        self.state = state
        self.get_mic_pub = get_mic_pub
        self.get_room = get_room
        # Optional so older callers / tests that don't pass it still
        # work — POST /screen-share returns 503 in that case.
        self.get_screen_share = get_screen_share
        self.restart_agent_unit = restart_agent_unit
        self.log = log
        # SSE subscribers — see /events route + enqueue_event below.
        # Each entry is an asyncio.Queue owned by one live HTTP response
        # writer. Modified only from the asyncio loop (no locking needed).
        self._sse_subscribers: set[asyncio.Queue] = set()

    # ── Server bring-up ────────────────────────────────────────────

    def build_app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/status",  self.status)
        app.router.add_get("/level",   self.level)    # fast (~30fps) lip-sync poll
        app.router.add_get("/face",    self.face)    # per-frame viseme morph weights
        app.router.add_post("/face/feed", self.face_feed)  # external realtime → face
        app.router.add_get("/health",  self.status)   # systemd / launch.sh probe
        app.router.add_post("/mute",   self.mute)
        app.router.add_post("/speak",      self.speak)
        app.router.add_post("/stop",       self.stop)
        app.router.add_post("/user-input", self.user_input)
        app.router.add_post("/screen-share", self.screen_share)
        app.router.add_get("/screen-share/token", self.screen_share_token)
        app.router.add_get("/cli-model",   self.cli_model)
        app.router.add_post("/cli-model",  self.cli_model)
        app.router.add_get("/voice-model",   self.speech_model)
        app.router.add_post("/voice-model",  self.speech_model)
        app.router.add_get("/tts-provider",  self.tts_provider)
        app.router.add_post("/tts-provider", self.tts_provider)
        app.router.add_get("/events",      self.events)
        app.router.add_route("OPTIONS", "/{tail:.*}", self.cors)
        return app

    async def start_server(self, port: int = STATUS_PORT) -> web.AppRunner:
        """Bring up the status HTTP server alongside the LiveKit loop."""
        app = self.build_app()
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", port)
        await site.start()
        self.log.info(f"[http] status/command server on :{port}")
        return runner

    # ── Handlers ───────────────────────────────────────────────────

    async def status(self, _: web.Request) -> web.Response:
        """GET /status — snapshot of the current client state."""
        # Refresh cli_model + speech_model from disk on every poll. The
        # files are small, reads are cheap, and this avoids any
        # sync-with-tray race.
        self.state.cli_model    = read_cli_model()
        self.state.speech_model = read_speech_model()
        try:
            self.state.tts_provider = TTS_PROVIDER_FILE.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            self.state.tts_provider = ""
        # Cheap stat call — flag file is touched/removed by the agent's
        # tool wrappers around every run_jarvis_cli call.
        self.state.tool_running  = TOOL_BUSY_FILE.exists()
        self.state.silent_mode   = SILENT_MODE_FILE.exists()
        # Definitive thinking signal — but only when the agent isn't
        # actively speaking. If TTS is playing we know the agent finished
        # its LLM phase, so suppress agent_thinking even if the file
        # hasn't been cleared yet (avoids gold→blue→gold flicker between
        # `conversation_item_added` and the speaking-track event).
        self.state.agent_thinking = agent_is_thinking() and not self.state.speaking
        # Person tracker status — read from the JSON file written by
        # vision.person_tracker (if running). Inline to keep /status a single
        # coherent snapshot.
        try:
            import json as _json
            from pathlib import Path as _Path
            _tracker_path = _Path.home() / ".jarvis" / "person_tracker.json"
            if _tracker_path.exists():
                _tracker = _json.loads(_tracker_path.read_text(encoding="utf-8"))
            else:
                _tracker = {"person_detected": False, "primary_face": None, "fps": 0, "error": None}
        except Exception:
            _tracker = {"person_detected": False, "primary_face": None, "fps": 0, "error": None}
        payload = asdict(self.state)
        payload["person_tracker"] = _tracker
        # Publish mic-drain AGE (seconds since the SFU last consumed a mic
        # frame) instead of the raw monotonic timestamp, which is meaningless
        # off-process. None until the first drain. The health backstop
        # (bin/jarvis-voice-healthcheck) treats a large age while connected +
        # unmuted as a broken audio path (stale PortAudio after resume / dead
        # uplink after a net blip), which is idle-safe because silence still
        # drains.
        drain_ts = payload.pop("mic_last_drain_ts", 0.0) or 0.0
        payload["mic_last_drain_age_s"] = (
            round(time.monotonic() - drain_ts, 1) if drain_ts else None
        )
        return web.json_response(payload, headers=_CORS_HEADERS)

    async def level(self, _: web.Request) -> web.Response:
        """GET /level — just the 0..1 output amplitude, polled ~30fps by the
        kiosk to drive the WebGL face's jaw. Deliberately tiny (no disk reads)
        so it's cheap at high frequency."""
        return web.json_response({"level": round(self.state.output_level, 4)},
                                 headers=_CORS_HEADERS)

    async def face(self, _: web.Request) -> web.Response:
        """GET /face — the current frame's ARKit-morph weights
        {target_N: 0..1} plus the raw level, polled ~30-60fps by the
        kiosk to drive the WebGL face's visemes. Empty weights = at rest;
        the kiosk then falls back to amplitude jaw from `level`."""
        return web.json_response(
            {"weights": getattr(self.state, "face_weights", {}) or {},
             "level": round(self.state.output_level, 4)},
            headers=_CORS_HEADERS,
        )

    async def face_feed(self, req: web.Request) -> web.Response:
        """POST /face/feed  {text?, level?, speaking?}  → drive the kiosk face
        from an EXTERNAL realtime speech source.

        The realtime modes (bin/jarvis-gpt-tools = OpenAI Realtime,
        bin/jarvis-gemini-tools = Gemini Live) play their own audio and never
        touch LiveKit, so the playback loop that normally feeds the face never
        runs for them — the face would freeze (only the Claude provider
        animated). They POST their output-audio level + spoken transcript here;
        we stash the raw inputs on `state` and stamp `ext_face_ts`. The
        voice-client's _external_face_ticker consumes them, runs the SAME
        viseme + expression engines the Claude path uses, and republishes on
        /face + /level. No connection gate — this works while the voice-client
        is muted in realtime mode."""
        try:
            body = await req.json()
        except Exception:
            body = {}
        st = self.state
        if "text" in body:
            st.ext_face_text = str(body.get("text") or "")
        if "level" in body:
            try:
                st.ext_face_level = max(0.0, min(1.0, float(body.get("level") or 0.0)))
            except (TypeError, ValueError):
                pass
        if "speaking" in body:
            st.ext_face_speaking = bool(body.get("speaking"))
        st.ext_face_ts = time.monotonic()
        return web.json_response(
            {"ok": True},
            headers={"Access-Control-Allow-Origin": "*"},
        )

    async def mute(self, req: web.Request) -> web.Response:
        """POST /mute  body={mute: bool}  → toggle local mic track mute.

        We mute at the track-publication layer rather than stopping
        the PortAudio stream so re-joining is instant (no sample-rate
        / device re-open latency). LiveKit carries the mute bit to
        the agent, which stops running STT on our (now-silent) audio.
        """
        try:
            body = await req.json()
        except Exception:
            body = {}
        # A USER toggle (tray "mute" button) sends NO `mute` field; the
        # mode watchdog (bin/jarvis-mode) sends an EXPLICIT one every ~10s
        # to keep JARVIS-Claude's mic muted while a direct mode (Gemini/
        # OpenAI) is the active voice. Only the user toggle expresses
        # "make the voice I HEAR go quiet" — so only it drives the
        # universal silent-mode flag (below). Without this distinction the
        # watchdog's mic-mute would also silence the direct voice forever.
        user_toggle = "mute" not in body
        mic_pub = self.get_mic_pub()
        # Is a direct voice (Gemini/OpenAI) ACTUALLY live right now? Liveness-
        # checked, not just the active-mode file — a stale file (backend died
        # without `jarvis-mode jarvis`) must not wedge the mic.
        live_direct = _direct_mode_active()
        if user_toggle:
            if live_direct:
                # A live direct voice owns Claude's mic (the watchdog drives
                # self.state.muted every ~10s, so it is NOT user intent here).
                # The toggle controls only the silent flag — derive direction
                # from the flag, as before.
                target = not SILENT_MODE_FILE.exists()
            else:
                # JARVIS mode, or a STALE/dead direct mode. The mic's own state
                # IS the user's intent now. If JARVIS is quiet by EITHER the mic
                # OR the silent flag, UNMUTE both; else mute both. This fixes
                # "mic muted but .silent-mode absent": the desktop overlay mutes
                # the mic via an explicit {mute:true} that never writes the flag,
                # so the old `not SILENT_MODE_FILE.exists()` computed the WRONG
                # direction and the tray toggle could never unmute.
                target = not (bool(self.state.muted) or SILENT_MODE_FILE.exists())
                # Stale direct-mode wedge: file says gemini/openai but the
                # backend is dead. Revert to jarvis so the Claude AGENT
                # un-silences too — else the mic unmutes but _is_silent() keeps
                # Claude dormant (the deaf+mute wedge, 2026-06-13).
                if _active_mode() in ("gemini", "openai"):
                    _write_active_mode("jarvis")
        else:
            target = bool(body.get("mute"))
        try:
            # OUTPUT mute. SILENT_MODE_FILE is the one signal every voice
            # honors: the Claude agent suppresses its TTS (and proactive
            # say()), and the Gemini/OpenAI direct tools pause mic-send +
            # drop audio-out. Muting the LiveKit mic track alone only stops
            # JARVIS from HEARING — this is what stops him TALKING. Write
            # the file directly (authoritative, works even if the agent is
            # busy) on user toggles only.
            if user_toggle:
                try:
                    if target:
                        SILENT_MODE_FILE.parent.mkdir(parents=True, exist_ok=True)
                        SILENT_MODE_FILE.write_text("on\n", encoding="utf-8")
                    else:
                        SILENT_MODE_FILE.unlink(missing_ok=True)
                    self.state.silent_mode = target
                except Exception as e:
                    self.log.warning("mute: silent-mode file write failed: %s", e)
                # Also nudge the agent to interrupt its CURRENT utterance now
                # (the file only gates FUTURE turns). Best-effort.
                room = self.get_room()
                if room is not None:
                    try:
                        payload = json.dumps(
                            {"type": "silent", "on": target}
                        ).encode("utf-8")
                        await room.local_participant.publish_data(
                            payload, reliable=True
                        )
                    except Exception as e:
                        self.log.warning(
                            "mute: silent-toggle publish failed: %s", e
                        )
            # Mic track mute (input side). LocalAudioTrack.mute/unmute are
            # sync in livekit-rtc Python — they flip a flag the engine picks
            # up on the next audio frame. Skip gracefully if not connected;
            # the output mute above is the part the user actually hears.
            #
            # In a DIRECT mode (Gemini/OpenAI) JARVIS-Claude's mic is owned by
            # the mode watchdog (bin/jarvis-mode) which keeps it muted so only
            # the direct voice answers. A USER toggle must NOT unmute it — doing
            # so let Claude hear "Jarvis" and answer ("Yes?") ON TOP of the
            # direct voice for the ~10s until the watchdog re-muted ("Jarvis
            # started talking when I clicked mute"). So for user toggles in a
            # direct mode, leave the mic to the watchdog and only drive the
            # silent flag above (which mutes the direct voice + Claude TTS). The
            # watchdog's own explicit calls still flow through to keep it muted.
            # Skip the mic ONLY when a LIVE direct voice owns it (the watchdog
            # keeps it muted). A stale/dead direct mode (live_direct False) does
            # NOT skip — so the toggle can recover a wedged mic.
            skip_mic = user_toggle and live_direct
            if mic_pub is not None and not skip_mic:
                if target:
                    mic_pub.track.mute()
                else:
                    mic_pub.track.unmute()
                self.state.muted = target
            return web.json_response(
                {"muted": target, "silent": bool(SILENT_MODE_FILE.exists())},
                headers={"Access-Control-Allow-Origin": "*"},
            )
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def speak(self, req: web.Request) -> web.Response:
        """POST /speak {text} → ask the agent to voice `text` via its TTS.

        Under the hood we publish a LiveKit data-channel message that
        the agent is listening for (see jarvis_agent.py's data_received
        handler). The agent calls session.say(text) which streams TTS
        through the same audio track the conversation uses, so playback
        is a no-op on our side — we already subscribe to that track."""
        room = self.get_room()
        if room is None or not self.state.connected:
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
            await room.local_participant.publish_data(payload, reliable=True)
            return web.json_response(
                {"queued": True, "chars": len(text)},
                headers={"Access-Control-Allow-Origin": "*"},
            )
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def stop(self, _: web.Request) -> web.Response:
        """POST /stop → ask the agent to interrupt its current utterance."""
        room = self.get_room()
        if room is None or not self.state.connected:
            return web.json_response({"error": "not connected"}, status=503)
        try:
            payload = json.dumps({"type": "stop"}).encode("utf-8")
            await room.local_participant.publish_data(payload, reliable=True)
            return web.json_response(
                {"stopped": True},
                headers={"Access-Control-Allow-Origin": "*"},
            )
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def user_input(self, req: web.Request) -> web.Response:
        """POST /user-input {text} → inject `text` as a synthetic user
        turn into the active voice session.

        Distinct from /speak: /speak makes JARVIS read text aloud (TTS
        only, no LLM). /user-input feeds the text into the AgentSession
        as if it had come from STT — JARVIS's LLM processes it,
        generates a reply, and the reply gets voiced via TTS. The reply
        is voiced only; conversation is not persisted off-process."""
        room = self.get_room()
        if room is None or not self.state.connected:
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
            await room.local_participant.publish_data(payload, reliable=True)
            return web.json_response(
                {"queued": True, "chars": len(text)},
                headers={"Access-Control-Allow-Origin": "*"},
            )
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def screen_share(self, req: web.Request) -> web.Response:
        """POST /screen-share {start: bool} → toggle X11 → LiveKit publish.

        `start: true` spawns ffmpeg + publishes a SOURCE_SCREENSHARE
        video track to the current room. `start: false` (or omitted)
        tears the publisher down.

        OFF on every fresh process — desktop capture is opt-in. The
        body may omit `start` to default to a toggle.
        """
        cors = {"Access-Control-Allow-Origin": "*"}
        ss = self.get_screen_share() if self.get_screen_share else None
        if ss is None:
            return web.json_response(
                {"error": "screen-share unavailable in this build"},
                status=503, headers=cors,
            )
        room = self.get_room()
        if room is None or not self.state.connected:
            return web.json_response(
                {"error": "not connected"}, status=503, headers=cors,
            )
        try:
            body = await req.json()
        except Exception:
            body = {}
        # Default = toggle. Explicit `start: bool` wins.
        if "start" in body:
            target = bool(body["start"])
        else:
            target = not ss.is_active()
        # `source` (optional, added 2026-05-28 for the
        # ScreenSharePicker modal): selects monitor or window. Shape:
        # {"kind": "monitor", "x": <int>, "y": <int>, "w": <int>, "h": <int>}
        # OR {"kind": "window", "id": "0x...", "w": <int>, "h": <int>}.
        # Omitted → full X11 root (legacy default).
        source = body.get("source") if isinstance(body, dict) else None
        try:
            if target:
                await ss.start(room, source=source)
            else:
                await ss.stop()
            self.state.sharing_screen = ss.is_active()
            # Voice ack — ask the agent to speak the new state so the
            # user hears confirmation without having to look at the
            # tray. Fire-and-forget; if /speak fails the toggle still
            # succeeded and the tray label flips at the next poll.
            #
            # Caller can suppress this with `ack: false` in the POST
            # body — used by the supervisor's set_screen_share tool
            # so the supervisor composes its own one-line reply
            # instead of the user hearing two acks back-to-back
            # (one from this data-publish, one from the supervisor's
            # reply to the tool result). Default behavior is unchanged
            # (tray clicks still get the audible ack).
            if body.get("ack") is not False:
                try:
                    # No "sir" — persona drops butler register (see
                    # supervisor.md TONE section, 2026-05-09 overhaul).
                    phrase = (
                        "Screen sharing on."
                        if self.state.sharing_screen
                        else "Screen sharing off."
                    )
                    payload = json.dumps({"type": "speak", "text": phrase}).encode("utf-8")
                    await room.local_participant.publish_data(payload, reliable=True)
                except Exception as e:
                    self.log.debug(f"[screen-share] voice-ack publish failed: {e}")
            return web.json_response(
                {"sharing": self.state.sharing_screen}, headers=cors,
            )
        except FileNotFoundError as e:
            return web.json_response(
                {"error": f"capture backend unavailable: {e}"},
                status=500, headers=cors,
            )
        except Exception as e:
            return web.json_response(
                {"error": str(e)}, status=500, headers=cors,
            )

    async def screen_share_token(self, _: web.Request) -> web.Response:
        """GET /screen-share/token → {url, token, room, identity}

        Mints a LiveKit JWT for the Tauri webview to publish a
        screen-share track via the JS SDK's
        `room.localParticipant.setScreenShareEnabled(true)`. Triggers
        the OS-native screen picker (xdg-desktop-portal on Linux) —
        same UX as Google Meet / Zoom Web. The webview joins the
        same room as the voice-client with a DIFFERENT identity
        (so the two clients don't collide), publishes the chosen
        source, and the screen-share observer subscribes to it like
        any other SOURCE_SCREENSHARE track.
        """
        cors = {"Access-Control-Allow-Origin": "*"}
        try:
            from voice_client_auth import (
                URL, ROOM_NAME, SCREEN_SHARE_IDENTITY, mint_screen_share_token,
            )
        except Exception as e:
            return web.json_response(
                {"error": f"auth module unavailable: {e}"},
                status=503, headers=cors,
            )
        try:
            token = mint_screen_share_token()
        except RuntimeError as e:
            # API key/secret missing — surface as 503 so the webview
            # can show a useful error instead of a generic failure.
            return web.json_response(
                {"error": str(e)}, status=503, headers=cors,
            )
        except Exception as e:
            return web.json_response(
                {"error": str(e)}, status=500, headers=cors,
            )
        return web.json_response(
            {
                "url": URL,
                "token": token,
                "room": ROOM_NAME,
                "identity": SCREEN_SHARE_IDENTITY,
            },
            headers=cors,
        )

    async def cli_model(self, req: web.Request) -> web.Response:
        """GET  /cli-model                          → {"model": "<id>", "available": [...]}
        POST /cli-model {"model": "deepseek-chat"} → write the choice

        The model ID is whatever the CLI's jarvisModelRegistry.ts knows
        about. The voice-agent's run_jarvis_cli reads the file on every
        spawn, so the change takes effect on the next CLI invocation
        without restarting any process."""
        cors = {"Access-Control-Allow-Origin": "*"}
        if req.method == "GET":
            return web.json_response({
                "model":     read_cli_model(),
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
            self.state.cli_model = name
            return web.json_response({"model": name}, headers=cors)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500, headers=cors)

    async def speech_model(self, req: web.Request) -> web.Response:
        """GET  /voice-model                   → {"model": "<id>", "available": [...]}
        POST /voice-model {"model": "X"}    → write the choice + restart agent

        Switching speech model requires a quick agent restart (~5 s amber
        "JARVIS booting" in the pill) because AgentSession's LLM is built
        once at session start. The voice-client itself stays up — the
        SFU keeps the room alive and the new agent rejoins automatically."""
        cors = {"Access-Control-Allow-Origin": "*"}
        if req.method == "GET":
            return web.json_response({
                "model":     read_speech_model(),
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
            # No-op if value unchanged. Without this guard a stray
            # re-POST (e.g. the tray re-syncing on launch) would tear
            # down a live agent session — including any in-flight
            # subagent handoff.
            current = read_speech_model()
            if current == name:
                return web.json_response(
                    {"model": name, "restarting": False, "unchanged": True},
                    headers=cors,
                )
            SPEECH_MODEL_FILE.parent.mkdir(parents=True, exist_ok=True)
            SPEECH_MODEL_FILE.write_text(name + "\n", encoding="utf-8")
            self.state.speech_model = name
            # Fire-and-forget — agent restart takes ~3-5 s; the user
            # sees the pill flip to amber "JARVIS booting" and back to
            # green.
            asyncio.create_task(self.restart_agent_unit())
            return web.json_response(
                {"model": name, "restarting": True}, headers=cors,
            )
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500, headers=cors)

    async def tts_provider(self, req: web.Request) -> web.Response:
        """GET  /tts-provider                              → current provider + available list
        POST /tts-provider {"provider": "groq:troy"}    → write choice + restart agent"""
        cors = {"Access-Control-Allow-Origin": "*"}
        if req.method == "GET":
            try:
                current = TTS_PROVIDER_FILE.read_text(encoding="utf-8").strip()
            except FileNotFoundError:
                current = ""
            return web.json_response({
                "provider":  current,
                "available": TTS_PROVIDERS_AVAILABLE,
            }, headers=cors)

        # POST
        try:
            body = await req.json()
        except Exception:
            body = {}
        provider = (body.get("provider") or "").strip()
        if provider not in TTS_PROVIDERS_AVAILABLE:
            return web.json_response(
                {"error": f"unknown TTS provider: {provider!r}",
                 "available": TTS_PROVIDERS_AVAILABLE},
                status=400, headers=cors,
            )
        try:
            # No-op if value unchanged — same rationale as /voice-model.
            try:
                current = TTS_PROVIDER_FILE.read_text(encoding="utf-8").strip()
            except FileNotFoundError:
                current = ""
            if current == provider:
                return web.json_response(
                    {"provider": provider, "restarting": False, "unchanged": True},
                    headers=cors,
                )
            TTS_PROVIDER_FILE.parent.mkdir(parents=True, exist_ok=True)
            TTS_PROVIDER_FILE.write_text(provider + "\n", encoding="utf-8")
            asyncio.create_task(self.restart_agent_unit())
            return web.json_response(
                {"provider": provider, "restarting": True}, headers=cors,
            )
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500, headers=cors)

    async def events(self, req: web.Request) -> web.StreamResponse:
        """GET /events → Server-Sent Events stream of voice-agent events.

        Today the only published event type is `assistant_says` (each
        assistant turn emits one). Subscribers register an
        asyncio.Queue; on disconnect, the queue is removed.

        Frames are `data: {json}\\n\\n` per SSE spec. Per-subscriber
        queue is bounded to 64 events; on overflow `enqueue_event`
        drops oldest.
        """
        resp = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Access-Control-Allow-Origin": "*",
            },
        )
        await resp.prepare(req)
        queue: asyncio.Queue = asyncio.Queue(maxsize=64)
        self._sse_subscribers.add(queue)
        self.log.info(f"[events] subscriber connected ({len(self._sse_subscribers)} total)")
        try:
            while True:
                event = await queue.get()
                line = f"data: {json.dumps(event)}\n\n".encode("utf-8")
                await resp.write(line)
        except (asyncio.CancelledError, ConnectionResetError):
            pass
        except Exception as e:
            self.log.warning(f"[events] subscriber write failed: {type(e).__name__}: {e}")
        finally:
            self._sse_subscribers.discard(queue)
            self.log.info(f"[events] subscriber disconnected ({len(self._sse_subscribers)} remaining)")
        return resp

    def enqueue_event(self, event: dict) -> None:
        """Broadcast a JSON event to every live SSE subscriber.

        Safe to call from any callback running on the asyncio event
        loop (sync, non-blocking). On QueueFull, drops the oldest item
        and enqueues the new one so a stuck panel doesn't pin memory.

        Called from jarvis_voice_client.py's data_received hook when an
        `assistant_says` packet arrives from the agent participant.
        """
        # Snapshot the subscriber set — enqueue can race with /events'
        # finally block, and iterating a mutating set raises.
        for q in list(self._sse_subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                    q.put_nowait(event)
                except asyncio.QueueEmpty:
                    # Concurrently drained — give up; next event will retry.
                    pass

    async def cors(self, _: web.Request) -> web.Response:
        """OPTIONS preflight for any /... route."""
        return web.Response(status=204, headers={
            **_CORS_HEADERS,
            "Access-Control-Max-Age": "86400",
        })
