"""Tests for the /modes + /mode + /mode/create|update|delete HTTP endpoints
added to VoiceClientHttpApi in Task 4 (conversation-modes feature).

Uses the same aiohttp TestClient + TestServer harness as
test_voice_client_events_sse.py. The conversation_modes store is redirected
to a tmp_path file via monkeypatching so tests never touch ~/.jarvis."""
from __future__ import annotations

import json
import logging
import unittest.mock as mock

import pytest
from aiohttp.test_utils import TestClient, TestServer


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def modes_path(tmp_path, monkeypatch):
    """Redirect the modes store AND the six real ~/.jarvis setting files
    to tmp for the duration of the test.

    The POST /mode tests run conversation_modes.apply(), which writes
    voice-model / cli-model / tts-provider / ... — with only MODES_FILE
    patched, every full-suite run rewrote the REAL ~/.jarvis/voice-model
    to claude-haiku-4-5 (live 2026-07-01: the user's DeepSeek pick kept
    "reverting to claude"; one revert timestamped to the second of a
    suite finishing)."""
    p = tmp_path / "modes.json"
    monkeypatch.setattr("pipeline.conversation_modes.MODES_FILE", p)
    for name in ("voice-mode", "voice-model", "cli-model",
                 "tts-provider", "voice-tts-voice", "mode-allowed-tools"):
        monkeypatch.setattr(
            f"pipeline.conversation_modes._F_{name.replace('-', '_').upper()}",
            tmp_path / name,
        )
    return p


def _make_api():
    """Construct VoiceClientHttpApi with stub deps for HTTP-only tests."""
    from voice_client_http_api import VoiceClientHttpApi
    state = mock.MagicMock()
    return VoiceClientHttpApi(
        state=state,
        get_mic_pub=lambda: None,
        get_room=lambda: None,
        get_screen_share=lambda: None,
        restart_agent_unit=mock.AsyncMock(),
        log=logging.getLogger("test"),
    )


# ── GET /modes ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_modes_returns_full_doc(modes_path):
    api = _make_api()
    app = api.build_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/modes")
        assert resp.status == 200
        doc = await resp.json()
        assert "active" in doc
        assert "modes" in doc
        ids = [m["id"] for m in doc["modes"]]
        assert ids == ["deepseek", "claude", "local"]
        assert doc["active"] == "deepseek"


@pytest.mark.asyncio
async def test_get_modes_cors_header(modes_path):
    api = _make_api()
    app = api.build_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/modes")
        assert resp.headers.get("Access-Control-Allow-Origin") == "*"


# ── POST /mode (select + restart) ────────────────────────────────────


@pytest.mark.asyncio
async def test_post_mode_applies_mode_and_triggers_restart(modes_path):
    api = _make_api()
    app = api.build_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/mode", json={"id": "claude"})
        assert resp.status == 200
        body = await resp.json()
        assert body["ok"] is True
        assert body["restarting"] is True
        assert body["id"] == "claude"
    # The restart callable must have been scheduled.
    api.restart_agent_unit.assert_called_once()


@pytest.mark.asyncio
async def test_post_mode_local_refused_without_ollama_model(modes_path, monkeypatch):
    """Local mode with no pulled Ollama model → 409, nothing applied.

    2026-07-02 user rule: applying Local with no on-device LLM boots a
    dead agent. The gate probes the Ollama daemon."""
    monkeypatch.setattr("voice_client_http_api._ollama_has_models", lambda: False)
    api = _make_api()
    app = api.build_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/mode", json={"id": "local"})
        assert resp.status == 409
        body = await resp.json()
        assert "Ollama" in body["error"]
    api.restart_agent_unit.assert_not_called()


@pytest.mark.asyncio
async def test_post_mode_local_applies_when_ollama_model_present(modes_path, monkeypatch):
    monkeypatch.setattr("voice_client_http_api._ollama_has_models", lambda: True)
    api = _make_api()
    app = api.build_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/mode", json={"id": "local"})
        assert resp.status == 200
        assert (await resp.json())["ok"] is True
    api.restart_agent_unit.assert_called_once()


@pytest.mark.asyncio
async def test_post_mode_updates_active_in_store(modes_path):
    api = _make_api()
    app = api.build_app()
    async with TestClient(TestServer(app)) as client:
        await client.post("/mode", json={"id": "claude"})
        resp = await client.get("/modes")
        doc = await resp.json()
        assert doc["active"] == "claude"


@pytest.mark.asyncio
async def test_post_mode_unknown_id_returns_404(modes_path):
    api = _make_api()
    app = api.build_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/mode", json={"id": "nonexistent"})
        assert resp.status == 404
        body = await resp.json()
        assert "error" in body


@pytest.mark.asyncio
async def test_post_mode_missing_id_returns_400(modes_path):
    api = _make_api()
    app = api.build_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/mode", json={})
        assert resp.status == 400


# ── POST /mode/create ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mode_create_adds_mode(modes_path):
    api = _make_api()
    app = api.build_app()
    new_mode = {
        "id": "focus", "label": "Focus", "voice_mode": "cloud",
        "voice_model": "claude-haiku-4-5", "cli_model": "claude-sonnet-4-6",
        "tts_provider": "kokoro:af_bella", "tts_voice": "af_bella",
        "allowed_tools": ["clarify"],
    }
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/mode/create", json=new_mode)
        assert resp.status == 200
        body = await resp.json()
        assert body["ok"] is True
        # Verify it appears in the store via GET /modes.
        resp2 = await client.get("/modes")
        doc = await resp2.json()
        ids = [m["id"] for m in doc["modes"]]
        assert "focus" in ids


@pytest.mark.asyncio
async def test_mode_create_duplicate_returns_409(modes_path):
    api = _make_api()
    app = api.build_app()
    async with TestClient(TestServer(app)) as client:
        # "deepseek" is a builtin — creating it again must conflict.
        resp = await client.post("/mode/create", json={
            "id": "deepseek", "label": "Dupe", "voice_mode": "cloud",
            "voice_model": "x", "cli_model": "x", "tts_provider": "x",
            "tts_voice": "x", "allowed_tools": None,
        })
        assert resp.status == 409
        body = await resp.json()
        assert "error" in body


@pytest.mark.asyncio
async def test_mode_create_missing_id_returns_400(modes_path):
    api = _make_api()
    app = api.build_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/mode/create", json={"label": "No ID"})
        assert resp.status == 400


# ── POST /mode/update ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mode_update_patches_existing(modes_path):
    api = _make_api()
    app = api.build_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/mode/update",
                                 json={"id": "claude", "patch": {"label": "Claude Cloud"}})
        assert resp.status == 200
        assert (await resp.json())["ok"] is True
        # Verify the label changed.
        doc = (await (await client.get("/modes")).json())
        claude = next(m for m in doc["modes"] if m["id"] == "claude")
        assert claude["label"] == "Claude Cloud"


@pytest.mark.asyncio
async def test_mode_update_cannot_change_id(modes_path):
    api = _make_api()
    app = api.build_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/mode/update",
                                 json={"id": "claude", "patch": {"id": "HACKED", "label": "X"}})
        assert resp.status == 200
        doc = (await (await client.get("/modes")).json())
        ids = [m["id"] for m in doc["modes"]]
        assert "HACKED" not in ids
        assert "claude" in ids


@pytest.mark.asyncio
async def test_mode_update_unknown_id_returns_404(modes_path):
    api = _make_api()
    app = api.build_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/mode/update",
                                 json={"id": "ghost", "patch": {"label": "X"}})
        assert resp.status == 404


# ── POST /mode/delete ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mode_delete_removes_mode(modes_path):
    api = _make_api()
    app = api.build_app()
    async with TestClient(TestServer(app)) as client:
        # First create a mode to delete.
        await client.post("/mode/create", json={
            "id": "temp", "label": "Temp", "voice_mode": "cloud",
            "voice_model": "x", "cli_model": "x", "tts_provider": "x",
            "tts_voice": "x", "allowed_tools": None,
        })
        resp = await client.post("/mode/delete", json={"id": "temp"})
        assert resp.status == 200
        assert (await resp.json())["ok"] is True
        doc = (await (await client.get("/modes")).json())
        assert "temp" not in [m["id"] for m in doc["modes"]]


@pytest.mark.asyncio
async def test_mode_delete_active_returns_409(modes_path):
    api = _make_api()
    app = api.build_app()
    async with TestClient(TestServer(app)) as client:
        # Default active is "deepseek".
        resp = await client.post("/mode/delete", json={"id": "deepseek"})
        assert resp.status == 409
        body = await resp.json()
        assert "error" in body


@pytest.mark.asyncio
async def test_mode_delete_missing_id_returns_400(modes_path):
    api = _make_api()
    app = api.build_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/mode/delete", json={})
        assert resp.status == 400
