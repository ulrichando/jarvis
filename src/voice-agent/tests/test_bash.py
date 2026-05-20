"""Tests for the bash tool's env-secret scrubbing (added 2026-05-20).

The bwrap sandbox masks secret *files* but not env vars; `_scrub_env`
strips secret-shaped vars from the bash subprocess so a `network=True`
command can't exfil API keys / the LiveKit secret / the bridge token.
"""
from tools import bash


def test_scrub_env_strips_secrets_keeps_basics(monkeypatch):
    monkeypatch.setattr(bash, "_SCRUB_ENV", True)
    monkeypatch.setenv("GROQ_API_KEY", "sk-secret")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "lk-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-o")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_x")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("HOME", "/home/ulrich")
    env = bash._scrub_env()
    for k in ("GROQ_API_KEY", "LIVEKIT_API_SECRET", "OPENAI_API_KEY", "GITHUB_TOKEN"):
        assert k not in env, f"{k} should be scrubbed from the bash env"
    assert env["PATH"] == "/usr/bin:/bin"   # non-secret vars preserved
    assert env["HOME"] == "/home/ulrich"


def test_scrub_env_disabled_returns_none(monkeypatch):
    # JARVIS_BASH_SCRUB_ENV=0 → None → subprocess inherits the full env.
    monkeypatch.setattr(bash, "_SCRUB_ENV", False)
    assert bash._scrub_env() is None


def test_secret_regex_matches_secrets_not_basics():
    rx = bash._SECRET_ENV_RE
    for secret in ("GROQ_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
                   "DEEPSEEK_API_KEY", "GOOGLE_API_KEY", "LIVEKIT_API_SECRET",
                   "AWS_SECRET_ACCESS_KEY", "GITHUB_TOKEN", "DB_PASSWORD",
                   "LANGCHAIN_API_KEY", "JARVIS_LOCAL_API_TOKEN"):
        assert rx.search(secret), f"{secret} should be treated as secret"
    for benign in ("PATH", "HOME", "USER", "LANG", "DISPLAY", "SHELL", "TERM", "PWD"):
        assert not rx.search(benign), f"{benign} should NOT be scrubbed"
