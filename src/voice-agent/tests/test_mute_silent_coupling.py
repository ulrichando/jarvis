"""The desktop mute button must also stop JARVIS from TALKING, not just
from hearing.

Regression test for "I click mute but he can still talk". The voice-client
/mute handler distinguishes a USER toggle (tray button — no `mute` field)
from the mode watchdog's explicit mic-mute of JARVIS-Claude, and only the
user toggle drives SILENT_MODE_FILE — the one signal every voice honors
(the Claude agent suppresses TTS; the Gemini/OpenAI direct tools pause
mic-send + drop audio-out).

Covers:
  - user toggle (no `mute` field) writes/removes the silent flag
  - explicit {mute:true} (watchdog) does NOT touch the silent flag — so it
    can't permanently mute the active direct voice
  - the mic still toggles; a publish failure / missing room never 500s
"""
from __future__ import annotations

import json
import logging
import unittest.mock as mock

import pytest
from aiohttp.test_utils import TestClient, TestServer


@pytest.fixture()
def silent_file(tmp_path, monkeypatch):
    """Isolate SILENT_MODE_FILE + active-mode to tmp paths so tests don't read
    the real ~/.jarvis state and are deterministic. Defaults to JARVIS mode
    (no direct mode) unless a test writes the active-mode file."""
    import voice_client_http_api as mod

    f = tmp_path / ".silent-mode"
    monkeypatch.setattr(mod, "SILENT_MODE_FILE", f)
    monkeypatch.setattr(mod, "_ACTIVE_MODE_FILE", str(tmp_path / "active-mode"))
    return f


@pytest.fixture()
def set_mode(tmp_path):
    """Helper to write the (isolated) active-mode file."""
    def _w(mode: str) -> None:
        (tmp_path / "active-mode").write_text(mode, encoding="utf-8")
    return _w


def _make_api(*, muted_now: bool, room: object | None):
    from voice_client_http_api import VoiceClientHttpApi
    from dataclasses import dataclass

    @dataclass
    class _State:
        muted: bool = False
        silent_mode: bool = False

    state = _State(muted=muted_now)
    mic_pub = mock.MagicMock()  # mic_pub.track.mute()/unmute() are sync no-ops
    api = VoiceClientHttpApi(
        state=state,
        get_mic_pub=lambda: mic_pub,
        get_room=lambda: room,
        get_screen_share=lambda: None,
        restart_agent_unit=mock.AsyncMock(),
        log=logging.getLogger("test"),
    )
    return api, mic_pub


def _fake_room():
    room = mock.MagicMock()
    room.local_participant.publish_data = mock.AsyncMock()
    return room


def _published_silent(room) -> list[bool]:
    out: list[bool] = []
    for call in room.local_participant.publish_data.await_args_list:
        try:
            msg = json.loads(call.args[0].decode("utf-8"))
        except Exception:
            continue
        if msg.get("type") == "silent":
            out.append(bool(msg.get("on")))
    return out


@pytest.mark.asyncio
async def test_user_toggle_sets_then_clears_silent(silent_file):
    # A tray toggle sends NO `mute` field. First press → muted+silent ON.
    room = _fake_room()
    api, _mic = _make_api(muted_now=False, room=room)
    app = api.build_app()
    async with TestClient(TestServer(app)) as client:
        async with client.post("/mute", json={}) as resp:
            assert resp.status == 200
            body = await resp.json()
            assert body["muted"] is True and body["silent"] is True
    assert silent_file.exists()
    assert _published_silent(room) == [True]  # agent nudged to interrupt now


@pytest.mark.asyncio
async def test_user_untoggle_clears_silent(silent_file):
    silent_file.write_text("on\n")  # currently muted
    room = _fake_room()
    api, mic = _make_api(muted_now=True, room=room)
    app = api.build_app()
    async with TestClient(TestServer(app)) as client:
        async with client.post("/mute", json={}) as resp:
            assert resp.status == 200
            assert (await resp.json())["silent"] is False
    assert not silent_file.exists()
    mic.track.unmute.assert_called_once()
    assert _published_silent(room) == [False]


@pytest.mark.asyncio
async def test_watchdog_explicit_mute_does_not_touch_silent(silent_file):
    # The watchdog mutes JARVIS-Claude's mic with an EXPLICIT field every
    # ~10s while a direct mode runs. That must NOT engage the silent flag —
    # otherwise it would permanently mute the active Gemini/OpenAI voice.
    room = _fake_room()
    api, mic = _make_api(muted_now=False, room=room)
    app = api.build_app()
    async with TestClient(TestServer(app)) as client:
        async with client.post("/mute", json={"mute": True}) as resp:
            assert resp.status == 200
            assert (await resp.json())["muted"] is True
    assert not silent_file.exists()          # silent flag untouched
    mic.track.mute.assert_called_once()      # but the mic IS muted
    assert _published_silent(room) == []     # no silent packet published


@pytest.mark.asyncio
async def test_no_room_user_toggle_still_sets_silent(silent_file):
    # Not connected to LiveKit (no room): the output mute (silent flag) is
    # what the user hears, so it must still apply; mic toggle is best-effort.
    api, _mic = _make_api(muted_now=False, room=None)
    app = api.build_app()
    async with TestClient(TestServer(app)) as client:
        async with client.post("/mute", json={}) as resp:
            assert resp.status == 200
            assert (await resp.json())["silent"] is True
    assert silent_file.exists()


@pytest.mark.asyncio
async def test_publish_failure_does_not_500(silent_file):
    room = _fake_room()
    room.local_participant.publish_data = mock.AsyncMock(
        side_effect=RuntimeError("no transport")
    )
    api, _mic = _make_api(muted_now=False, room=room)
    app = api.build_app()
    async with TestClient(TestServer(app)) as client:
        async with client.post("/mute", json={}) as resp:
            assert resp.status == 200          # publish failure swallowed
    assert silent_file.exists()                # file write still happened


@pytest.mark.asyncio
async def test_direct_mode_user_toggle_does_not_unmute_claude_mic(
    silent_file, set_mode, monkeypatch
):
    # In a LIVE Gemini/OpenAI mode JARVIS-Claude's mic is owned by the
    # watchdog. A user mute toggle must drive the silent flag but NEVER touch
    # the mic — unmuting it let Claude answer "Yes?" over the direct voice.
    import voice_client_http_api as mod
    monkeypatch.setattr(mod, "_direct_unit_live", lambda _m: True)  # backend alive
    set_mode("gemini")
    silent_file.write_text("on\n")  # currently muted
    room = _fake_room()
    api, mic = _make_api(muted_now=True, room=room)
    app = api.build_app()
    async with TestClient(TestServer(app)) as client:
        async with client.post("/mute", json={}) as resp:  # unmute toggle
            assert resp.status == 200
            assert (await resp.json())["silent"] is False
    assert not silent_file.exists()              # silent flag cleared (Gemini)
    mic.track.unmute.assert_not_called()         # but Claude's mic untouched
    mic.track.mute.assert_not_called()


@pytest.mark.asyncio
async def test_jarvis_mode_mic_muted_without_silent_flag_unmutes(
    silent_file, set_mode, monkeypatch
):
    # THE bug behind "the unmute button does nothing": the desktop overlay
    # mutes the mic via an explicit {mute:true} that never writes .silent-mode,
    # so the mic is muted while the flag is ABSENT. A user toggle must read the
    # REAL state (mic muted) and UNMUTE — not derive 'mute' from the missing
    # flag and re-mute forever.
    import voice_client_http_api as mod
    monkeypatch.setattr(mod, "_direct_unit_live", lambda _m: False)
    set_mode("jarvis")
    assert not silent_file.exists()                  # flag absent…
    room = _fake_room()
    api, mic = _make_api(muted_now=True, room=room)  # …but the mic IS muted
    app = api.build_app()
    async with TestClient(TestServer(app)) as client:
        async with client.post("/mute", json={}) as resp:  # user toggle
            assert resp.status == 200
            assert (await resp.json())["muted"] is False     # → UNMUTE, not re-mute
    mic.track.unmute.assert_called_once()
    mic.track.mute.assert_not_called()


@pytest.mark.asyncio
async def test_stale_direct_mode_user_toggle_recovers_mic(
    silent_file, set_mode, tmp_path, monkeypatch
):
    # Wedge: active-mode says a direct mode but the backend is DEAD (killed
    # without `jarvis-mode jarvis`), leaving Claude's mic muted with no
    # recovery path. A user toggle must unmute the mic AND revert active-mode
    # to jarvis so the Claude agent's _is_silent() stops silencing it.
    import voice_client_http_api as mod
    monkeypatch.setattr(mod, "_direct_unit_live", lambda _m: False)  # backend dead
    set_mode("openai")
    room = _fake_room()
    api, mic = _make_api(muted_now=True, room=room)  # mic stuck muted
    app = api.build_app()
    async with TestClient(TestServer(app)) as client:
        async with client.post("/mute", json={}) as resp:
            assert resp.status == 200
            assert (await resp.json())["muted"] is False
    mic.track.unmute.assert_called_once()                 # mic recovered
    assert (tmp_path / "active-mode").read_text(
        encoding="utf-8"
    ).strip() == "jarvis"                                 # reverted for the agent


@pytest.mark.asyncio
async def test_jarvis_mode_user_toggle_still_drives_claude_mic(
    silent_file, set_mode
):
    # In JARVIS mode Claude IS the active voice, so the toggle must mute its
    # mic as before.
    set_mode("jarvis")
    room = _fake_room()
    api, mic = _make_api(muted_now=False, room=room)
    app = api.build_app()
    async with TestClient(TestServer(app)) as client:
        async with client.post("/mute", json={}) as resp:  # mute toggle
            assert resp.status == 200
            assert (await resp.json())["silent"] is True
    mic.track.mute.assert_called_once()


def test_mute_data_handlers_force_interrupt():
    """The agent-side mute paths (the `_on_data` 'stop' + 'silent' data
    packets) must call session.interrupt(force=True).

    Regression for "Claude is still talking while on mute": in echo-aware
    mode (the default, JARVIS_ECHO_AWARE_BARGEIN=1) the session sets
    turn_handling.interruption.enabled=False, and a speech's
    allow_interruptions falls back to that flag — so EVERY JARVIS utterance
    is non-interruptible. A bare session.interrupt() then raises
    "does not allow interruptions", which the handler's `except RuntimeError`
    swallows, leaving the current utterance playing. The mute button +
    bin/jarvis-mute (both hit /stop) therefore only suppressed FUTURE turns,
    never the in-flight sentence. force=True bypasses the guard.

    `_on_data` is a closure inside entrypoint() and can't be imported, so we
    assert the contract at the AST level (reformatting-robust).
    """
    import ast
    import inspect

    import jarvis_agent

    tree = ast.parse(inspect.getsource(jarvis_agent))
    handlers = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_on_data"
    ]
    assert handlers, "_on_data data-packet handler not found in jarvis_agent"

    checked = 0
    for handler in handlers:
        for call in ast.walk(handler):
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "interrupt"
            ):
                forced = any(
                    kw.arg == "force"
                    and isinstance(kw.value, ast.Constant)
                    and kw.value.value is True
                    for kw in call.keywords
                )
                assert forced, (
                    "session.interrupt() inside _on_data must pass force=True — "
                    "a bare interrupt() no-ops on JARVIS's non-interruptible "
                    "speeches (echo-aware mode), so the mute button can't cut the "
                    "current utterance ('still talking while on mute')"
                )
                checked += 1
    # The 'stop' and 'silent' handlers each interrupt → expect at least two.
    assert checked >= 2, (
        f"expected ≥2 forced interrupt() calls in _on_data, found {checked}"
    )


def test_silent_flag_roundtrip(tmp_path, monkeypatch):
    """The agent's silent-mode flag (set by the `silent` data packet) gates
    _is_silent(), which the proactive watchers consult."""
    import jarvis_agent

    monkeypatch.setattr(
        jarvis_agent, "_SILENT_MODE_FILE", tmp_path / ".silent-mode"
    )
    monkeypatch.setattr(
        jarvis_agent, "_ACTIVE_MODE_FILE", tmp_path / "active-mode"
    )
    assert jarvis_agent._is_silent() is False
    jarvis_agent._set_silent(True)
    assert jarvis_agent._is_silent() is True
    jarvis_agent._set_silent(False)
    assert jarvis_agent._is_silent() is False
    # A LIVE direct mode silences Claude even with no user mute flag.
    monkeypatch.setattr(jarvis_agent, "_direct_unit_live", lambda _m: True)
    (tmp_path / "active-mode").write_text("gemini", encoding="utf-8")
    assert jarvis_agent._is_silent() is True
    (tmp_path / "active-mode").write_text("jarvis", encoding="utf-8")
    assert jarvis_agent._is_silent() is False


def test_dead_direct_mode_does_not_silence_claude(tmp_path, monkeypatch):
    """Auto-recovery: a stale active-mode file (the direct backend died without
    `jarvis-mode jarvis`) must NOT keep Claude dormant — _is_silent() returns
    False once the backend is gone, so the Claude voice resumes by itself."""
    import jarvis_agent

    monkeypatch.setattr(
        jarvis_agent, "_SILENT_MODE_FILE", tmp_path / ".silent-mode"
    )
    monkeypatch.setattr(
        jarvis_agent, "_ACTIVE_MODE_FILE", tmp_path / "active-mode"
    )
    monkeypatch.setattr(jarvis_agent, "_direct_unit_live", lambda _m: False)  # dead
    (tmp_path / "active-mode").write_text("openai", encoding="utf-8")
    assert jarvis_agent._is_silent() is False    # un-silences (no wedge)
    # …but an explicit user mute flag still silences, regardless of mode.
    jarvis_agent._set_silent(True)
    assert jarvis_agent._is_silent() is True
