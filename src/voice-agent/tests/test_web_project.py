"""Tests for the web_project voice tool.

Patches tools._web_api.request_web (the single HTTP seam). Covers list, create,
name→id resolution for configure/delete, favorite toggle, the 204-no-content
delete path (exercises call_web's empty-body handling), and failure surfacing.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest


def _resp(status=200, body=None, text=None):
    r = AsyncMock()
    r.status_code = status
    r.json = lambda: (body if body is not None else {})
    if text is not None:
        r.text = text
    else:
        r.text = json.dumps(body) if body is not None else ""
    return r


@pytest.mark.asyncio
async def test_list_formats_summary():
    from tools.web_project import _handle_web_project

    body = {"projects": [{"id": "1", "name": "Taxes"}, {"id": "2", "name": "Ideas"}]}
    with patch("tools._web_api.request_web", new=AsyncMock(return_value=_resp(body=body))):
        out = await _handle_web_project({"action": "list"})
    assert "Taxes" in out and "Ideas" in out and "2" in out


@pytest.mark.asyncio
async def test_create_posts_name_and_optional_fields():
    from tools.web_project import _handle_web_project

    body = {"project": {"id": "x", "name": "Taxes"}}
    with patch("tools._web_api.request_web", new=AsyncMock(return_value=_resp(status=201, body=body))) as m:
        out = await _handle_web_project(
            {"action": "create", "name": "Taxes", "instructions": "be terse"}
        )
    assert "Taxes" in out
    sent = m.await_args.kwargs["json_body"]
    assert sent["name"] == "Taxes" and sent["instructions"] == "be terse"


@pytest.mark.asyncio
async def test_create_without_name_errors():
    from tools.web_project import _handle_web_project

    out = await _handle_web_project({"action": "create"})
    assert "error" in json.loads(out)


@pytest.mark.asyncio
async def test_favorite_via_configure():
    from tools.web_project import _handle_web_project

    async def fake(method, path, *, json_body=None, params=None, timeout=15.0):
        if method.upper() == "PATCH":
            assert json_body == {"isFavorite": True}
            return _resp(body={"project": {"id": "id-1", "name": "Taxes"}})
        return _resp(body={})

    with patch("tools._web_api.request_web", new=fake):
        out = await _handle_web_project(
            {"action": "configure", "id": "id-1", "favorite": True}
        )
    assert "Favorited" in out and "Taxes" in out


@pytest.mark.asyncio
async def test_delete_resolves_name_and_handles_204():
    from tools.web_project import _handle_web_project

    calls = []

    async def fake(method, path, *, json_body=None, params=None, timeout=15.0):
        calls.append((method.upper(), path))
        if method.upper() == "GET":
            return _resp(body={"projects": [{"id": "pid-9", "name": "Scratch"}]})
        # DELETE → real web returns 204 no-content; call_web must treat as success
        return _resp(status=204, text="")

    with patch("tools._web_api.request_web", new=fake):
        out = await _handle_web_project({"action": "delete", "name": "Scratch"})
    assert "Deleted" in out and "Scratch" in out
    assert ("DELETE", "/api/projects/pid-9") in calls


@pytest.mark.asyncio
async def test_delete_ambiguous_refuses():
    from tools.web_project import _handle_web_project

    body = {"projects": [{"id": "1", "name": "Dup"}, {"id": "2", "name": "Dup"}]}
    with patch("tools._web_api.request_web", new=AsyncMock(return_value=_resp(body=body))):
        out = await _handle_web_project({"action": "delete", "name": "Dup"})
    assert "more than one" in json.loads(out)["error"].lower()


@pytest.mark.asyncio
async def test_auth_rejected_is_spoken():
    from tools.web_project import _handle_web_project

    with patch("tools._web_api.request_web", new=AsyncMock(return_value=_resp(status=401))):
        out = await _handle_web_project({"action": "list"})
    assert "auth" in json.loads(out)["error"].lower()


@pytest.mark.asyncio
async def test_web_down_is_spoken():
    from tools.web_project import _handle_web_project

    async def boom(*a, **k):
        raise httpx.ConnectError("refused")

    with patch("tools._web_api.request_web", new=boom):
        out = await _handle_web_project({"action": "list"})
    assert "couldn't reach" in json.loads(out)["error"].lower() or "web app" in json.loads(out)["error"].lower()
