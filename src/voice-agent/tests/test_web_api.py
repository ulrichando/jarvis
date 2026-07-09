"""Tests for the shared _web_api layer (URL + token selection)."""
from __future__ import annotations

import pytest

from tools import _web_api


def _clear(monkeypatch):
    for k in ("JARVIS_WEB_URL", "JARVIS_WEB_TOKEN", "JARVIS_LOCAL_API_TOKEN"):
        monkeypatch.delenv(k, raising=False)


def test_web_url_defaults_local(monkeypatch):
    _clear(monkeypatch)
    assert _web_api.web_url() == "http://127.0.0.1:3000"


def test_web_url_override_strips_trailing_slash(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("JARVIS_WEB_URL", "https://0wlan.com/")
    assert _web_api.web_url() == "https://0wlan.com"


def test_auth_prefers_web_token_over_local(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("JARVIS_LOCAL_API_TOKEN", "local-tok")
    monkeypatch.setenv("JARVIS_WEB_TOKEN", "remote-tok")
    assert _web_api.auth_headers() == {"Authorization": "Bearer remote-tok"}


def test_auth_falls_back_to_local_token(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("JARVIS_LOCAL_API_TOKEN", "local-tok")
    assert _web_api.auth_headers() == {"Authorization": "Bearer local-tok"}


def test_auth_empty_when_no_token(monkeypatch):
    _clear(monkeypatch)
    assert _web_api.auth_headers() == {}
