"""_stream_up + /health streamUp field (plan: cloud computer-use desktop)."""
import asyncio
import contextlib
import json

import computer_use_service as svc


def test_stream_up_true(monkeypatch):
    monkeypatch.setattr(svc.socket, "create_connection", lambda *a, **k: contextlib.nullcontext())
    assert svc._stream_up(port=6080) is True


def test_stream_up_false(monkeypatch):
    def boom(*a, **k):
        raise OSError("connection refused")
    monkeypatch.setattr(svc.socket, "create_connection", boom)
    assert svc._stream_up(port=6080) is False


def test_health_includes_streamUp(monkeypatch):
    monkeypatch.setattr(svc.socket, "create_connection", lambda *a, **k: contextlib.nullcontext())
    resp = asyncio.run(svc._health(None))
    data = json.loads(resp.text)
    assert data["streamUp"] is True
    assert "providers" in data and "x11" in data
