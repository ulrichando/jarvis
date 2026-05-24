"""Tests for tools.file_safety — the write-denylist guarding the direct
file tools (write/edit) against prompt-injected writes.

Threat model (CLAUDE.md): text reaching the supervisor LLM via the user's
mic can call write()/edit() on any path the 'ulrich' user can write. bash
is already bwrap-sandboxed + secret-masked, but write/edit had NO path
guard — a single injected `write("~/.ssh/authorized_keys", ...)` or an
edit of `~/.bashrc` / a systemd unit / `~/.claude` would persist or
escalate. These tests lock the denial behavior so that can't happen.

Ported/adapted from hermes/agent/file_safety.py (2026-05-20).

Safety note for the test author: the integration tests monkeypatch HOME to
a tmp dir, so even a regression that lets the write through only ever
touches a throwaway tmp path — never the real ~/.ssh.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


# (A defensive `_reset_plan_mode` autouse fixture used to live here, left
# over from when these tests shared a module with the plan-mode tests.
# tools.file_safety has no plan-mode coupling — is_write_denied /
# is_read_denied never consult plan-mode state — and the plan_mode tool
# module was removed in the registry-only supervisor refactor, so the
# fixture is gone.)


# ── Pure predicate: exact-path denials (against the real home) ─────────


def test_denies_ssh_authorized_keys():
    from tools import file_safety

    assert file_safety.is_write_denied("~/.ssh/authorized_keys") is True


def test_denies_ssh_private_keys():
    from tools import file_safety

    assert file_safety.is_write_denied("~/.ssh/id_ed25519") is True
    assert file_safety.is_write_denied("~/.ssh/id_rsa") is True


def test_denies_shell_init_files():
    from tools import file_safety

    for rc in ("~/.bashrc", "~/.zshrc", "~/.profile", "~/.zshenv"):
        assert file_safety.is_write_denied(rc) is True, rc


def test_denies_gitconfig():
    # .gitconfig aliases can run arbitrary commands on `git` invocation.
    from tools import file_safety

    assert file_safety.is_write_denied("~/.gitconfig") is True


def test_denies_sudoers_and_passwd():
    from tools import file_safety

    assert file_safety.is_write_denied("/etc/sudoers") is True
    assert file_safety.is_write_denied("/etc/sudoers.d/jarvis") is True
    assert file_safety.is_write_denied("/etc/passwd") is True


def test_denies_voice_agent_env():
    # The voice-agent's own .env holds DEEPGRAM_API_KEY etc.
    from tools import file_safety

    assert file_safety.is_write_denied(file_safety._VOICE_AGENT_ENV) is True


def test_denies_jarvis_api_token():
    from tools import file_safety
    from tools.runtime import get_jarvis_home

    assert file_safety.is_write_denied(str(get_jarvis_home() / "local-api-token.env")) is True


# ── Pure predicate: prefix (directory-tree) denials ────────────────────


def test_denies_ssh_dir_subpaths():
    from tools import file_safety

    assert file_safety.is_write_denied("~/.ssh/some_new_key") is True


def test_denies_gnupg_and_aws_dirs():
    from tools import file_safety

    assert file_safety.is_write_denied("~/.gnupg/anything") is True
    assert file_safety.is_write_denied("~/.aws/credentials") is True


def test_denies_claude_config_dir():
    # ~/.claude holds settings.json + hooks — an escalation vector the
    # voice agent must never write.
    from tools import file_safety

    assert file_safety.is_write_denied("~/.claude/settings.json") is True
    assert file_safety.is_write_denied("~/.claude/hooks/evil.sh") is True


def test_denies_systemd_user_units():
    # ~/.config/systemd/user/*.service = persistence vector.
    from tools import file_safety

    target = "~/.config/systemd/user/jarvis-voice-agent.service"
    assert file_safety.is_write_denied(target) is True


# ── Pure predicate: things that must stay ALLOWED (no over-blocking) ───


def test_allows_ordinary_paths():
    from tools import file_safety

    assert file_safety.is_write_denied("/tmp/scratch.txt") is False
    assert file_safety.is_write_denied("~/Documents/notes.txt") is False


def test_allows_jarvis_user_dir():
    # JARVIS home has legitimate writable state (memories, voice-model
    # pref, etc.). Only the token file inside it is denied — not the
    # whole tree.
    from tools import file_safety
    from tools.runtime import get_jarvis_home

    jhome = get_jarvis_home()
    assert file_safety.is_write_denied(str(jhome / "memories" / "MEMORY.md")) is False
    assert file_safety.is_write_denied(str(jhome / "voice-model")) is False


def test_allows_other_config_subdirs():
    # Only .config/gh and .config/systemd are denied; the rest of
    # ~/.config stays writable.
    from tools import file_safety

    assert file_safety.is_write_denied("~/.config/myapp/settings.toml") is False


def test_empty_path_is_not_denied():
    from tools import file_safety

    assert file_safety.is_write_denied("") is False


# ── Symlink safety (realpath resolution) ───────────────────────────────


def test_symlink_into_denied_dir_is_denied(tmp_path, monkeypatch):
    from tools import file_safety

    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".ssh").mkdir()
    link = tmp_path / "innocent_link"
    link.symlink_to(tmp_path / ".ssh" / "authorized_keys")

    # realpath(link) resolves into ~/.ssh → must be denied despite the
    # benign-looking link name.
    assert file_safety.is_write_denied(str(link)) is True


# ── Optional JARVIS_WRITE_SAFE_ROOT confinement ────────────────────────


def test_safe_root_confines_writes(tmp_path, monkeypatch):
    from tools import file_safety

    monkeypatch.setenv("JARVIS_WRITE_SAFE_ROOT", str(tmp_path))

    # Inside the safe root → allowed.
    assert file_safety.is_write_denied(str(tmp_path / "ok.txt")) is False
    # Outside the safe root → denied even though it's an ordinary path.
    assert file_safety.is_write_denied("/var/tmp/elsewhere.txt") is True
    # Denylist still wins inside the safe root (defense in depth).
    assert file_safety.is_write_denied("/etc/passwd") is True


# ── write_denial_message shape ─────────────────────────────────────────


def test_write_denial_message_for_denied_path():
    from tools import file_safety

    msg = file_safety.write_denial_message("~/.ssh/authorized_keys")
    assert msg is not None
    assert msg.startswith("Error:")
    assert "refusing" in msg.lower()


def test_write_denial_message_none_for_allowed_path():
    from tools import file_safety

    assert file_safety.write_denial_message("/tmp/fine.txt") is None


# ── Read-of-secrets denial (exfiltration guard) ────────────────────────


def test_denies_read_ssh_private_key():
    from tools import file_safety

    assert file_safety.is_read_denied("~/.ssh/id_ed25519") is True


def test_denies_read_cloud_and_vcs_creds():
    from tools import file_safety

    assert file_safety.is_read_denied("~/.aws/credentials") is True
    assert file_safety.is_read_denied("~/.git-credentials") is True
    assert file_safety.is_read_denied("~/.config/gh/hosts.yml") is True


def test_denies_read_dotfile_secrets():
    from tools import file_safety

    assert file_safety.is_read_denied("~/.netrc") is True
    assert file_safety.is_read_denied("~/.pgpass") is True


def test_denies_read_jarvis_and_voice_env():
    from tools import file_safety
    from tools.runtime import get_jarvis_home

    assert file_safety.is_read_denied(str(get_jarvis_home() / "local-api-token.env")) is True
    assert file_safety.is_read_denied(file_safety._VOICE_AGENT_ENV) is True


def test_read_deny_is_narrower_than_write_deny():
    # Reading shell-init / gitconfig is NOT a secret leak — only WRITE
    # denies those (persistence). Read-deny targets credentials only.
    from tools import file_safety

    assert file_safety.is_read_denied("~/.bashrc") is False
    assert file_safety.is_read_denied("~/.gitconfig") is False


def test_allows_read_ordinary_paths():
    from tools import file_safety

    assert file_safety.is_read_denied("/tmp/scratch.txt") is False
    assert file_safety.is_read_denied("~/Documents/notes.txt") is False


def test_read_denial_message_shape():
    from tools import file_safety

    msg = file_safety.read_denial_message("~/.ssh/id_rsa")
    assert msg is not None
    assert msg.startswith("Error:")
    assert "refusing" in msg.lower()
    assert file_safety.read_denial_message("/tmp/fine.txt") is None


