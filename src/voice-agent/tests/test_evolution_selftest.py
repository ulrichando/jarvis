"""Tests for the self-evolution smoke-turn helper."""
from __future__ import annotations

import os
from pathlib import Path


def test_selftest_loads_voice_agent_env_stack(tmp_path, monkeypatch):
    from pipeline.automod import selftest

    repo = tmp_path / "repo"
    va = repo / "src" / "voice-agent"
    cli = repo / "src" / "cli"
    module_file = va / "pipeline" / "automod" / "selftest.py"
    module_file.parent.mkdir(parents=True)
    cli.mkdir(parents=True)
    home = tmp_path / "home"
    (home / ".jarvis").mkdir(parents=True)

    (va / ".env").write_text(
        "VOICE_ONLY=voice\nSTACK_VALUE=voice\nPRESET=file\n",
        encoding="utf-8",
    )
    (repo / ".env").write_text(
        "ROOT_ONLY=root\nSTACK_VALUE=root\n",
        encoding="utf-8",
    )
    (cli / ".env.local").write_text(
        "CLI_ONLY=cli\nSTACK_VALUE=cli\n",
        encoding="utf-8",
    )
    (home / ".jarvis" / "keys.env").write_text(
        "KEY_ONLY=key\nSTACK_VALUE=key\nPRESET=file-key\n",
        encoding="utf-8",
    )

    for key in ("VOICE_ONLY", "ROOT_ONLY", "CLI_ONLY", "KEY_ONLY", "STACK_VALUE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PRESET", "external")
    monkeypatch.setattr(selftest, "__file__", str(module_file))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    selftest._load_keys()

    assert os.environ["VOICE_ONLY"] == "voice"
    assert os.environ["ROOT_ONLY"] == "root"
    assert os.environ["CLI_ONLY"] == "cli"
    assert os.environ["KEY_ONLY"] == "key"
    assert os.environ["STACK_VALUE"] == "key"
    assert os.environ["PRESET"] == "external"
