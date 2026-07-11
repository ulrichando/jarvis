"""Installed-model discovery for local voice mode.

Local mode must NEVER hand Ollama a tag that isn't pulled (it 404s → the
voice brain goes mute). resolve_installed_model_tag() picks from what's
actually installed — the same `ollama list` discovery web + CLI use. These
tests monkeypatch the installed list so they're hermetic (no real ollama)."""
from providers import local_model_picker as p


def test_prefers_pulled_exact(monkeypatch):
    monkeypatch.setattr(p, "list_installed_tags",
                        lambda: ["qwen2.5:7b", "qwen3:30b-a3b"])
    # The validated pick wins when it's actually pulled.
    assert p.resolve_installed_model_tag("qwen3:30b-a3b") == "qwen3:30b-a3b"


def test_preferred_missing_falls_to_installed(monkeypatch):
    installed = ["qwen2.5:7b", "llama3.1:8b"]
    monkeypatch.setattr(p, "list_installed_tags", lambda: installed)
    # preferred NOT pulled → must return something that IS pulled, never the
    # 404 tag. (This is the bug that muted local mode on a box without it.)
    got = p.resolve_installed_model_tag("qwen3:30b-a3b")
    assert got in installed


def test_none_when_nothing_installed(monkeypatch):
    monkeypatch.setattr(p, "list_installed_tags", lambda: [])
    # Nothing pulled → None so the caller decides the fallback (never invents a tag).
    assert p.resolve_installed_model_tag("qwen3:30b-a3b") is None


def test_no_preference_still_returns_installed(monkeypatch):
    installed = ["llama3.1:8b"]
    monkeypatch.setattr(p, "list_installed_tags", lambda: installed)
    assert p.resolve_installed_model_tag("") in installed
