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
    """Isolate SILENT_MODE_FILE to a tmp path so tests don't touch the real
    ~/.jarvis/.silent-mode and are deterministic."""
    import voice_client_http_api as mod

    f = tmp_path / ".silent-mode"
    monkeypatch.setattr(mod, "SILENT_MODE_FILE", f)
    return f


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


def test_silent_flag_roundtrip(tmp_path, monkeypatch):
    """The agent's silent-mode flag (set by the `silent` data packet) gates
    _is_silent(), which the proactive watchers consult."""
    import jarvis_agent

    monkeypatch.setattr(
        jarvis_agent, "_SILENT_MODE_FILE", tmp_path / ".silent-mode"
    )
    assert jarvis_agent._is_silent() is False
    jarvis_agent._set_silent(True)
    assert jarvis_agent._is_silent() is True
    jarvis_agent._set_silent(False)
    assert jarvis_agent._is_silent() is False
