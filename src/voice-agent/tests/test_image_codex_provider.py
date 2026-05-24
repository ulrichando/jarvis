"""OpenAI-Codex image backend port — token resolution + gating + discovery.

OpenAI (API key) + xAI image backends already ship in tools/image_gen.py; this
guards the Codex OAuth variant: a JARVIS-native token reader (env var → Codex CLI
auth file with JWT-expiry skip) and inert-without-token gating.
"""
import base64
import importlib.util
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def _load():
    spec = importlib.util.spec_from_file_location(
        "_t_codex", Path(__file__).parent.parent / "plugins/image_gen/openai-codex/__init__.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_codex_provider_unavailable_without_token(monkeypatch, tmp_path):
    monkeypatch.delenv("CODEX_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("CODEX_AUTH_FILE", str(tmp_path / "nope.json"))  # nonexistent
    prov = _load().OpenAICodexImageGenProvider()
    assert prov.name == "openai-codex"
    assert prov.is_available() is False


def test_codex_token_read_from_env(monkeypatch):
    monkeypatch.setenv("CODEX_ACCESS_TOKEN", "tok-123")
    assert _load()._read_codex_access_token() == "tok-123"


def test_codex_token_read_from_auth_file(monkeypatch, tmp_path):
    monkeypatch.delenv("CODEX_ACCESS_TOKEN", raising=False)
    p = tmp_path / "auth.json"
    p.write_text(json.dumps({"tokens": {"access_token": "file-tok"}}))
    monkeypatch.setenv("CODEX_AUTH_FILE", str(p))
    assert _load()._read_codex_access_token() == "file-tok"


def test_codex_skips_expired_token(monkeypatch, tmp_path):
    monkeypatch.delenv("CODEX_ACCESS_TOKEN", raising=False)
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
    payload = base64.urlsafe_b64encode(
        json.dumps({"exp": int(time.time()) - 100}).encode()
    ).decode().rstrip("=")
    tok = f"{header}.{payload}.sig"
    p = tmp_path / "auth.json"
    p.write_text(json.dumps({"tokens": {"access_token": tok}}))
    monkeypatch.setenv("CODEX_AUTH_FILE", str(p))
    assert _load()._read_codex_access_token() is None


def test_codex_plugin_discovers():
    from tools.plugin_system import discover_plugins

    rows = {p["key"]: p for p in discover_plugins(force=True).list_plugins()}
    assert "image_gen/openai-codex" in rows
    assert rows["image_gen/openai-codex"]["enabled"] is True
    assert rows["image_gen/openai-codex"]["error"] is None
