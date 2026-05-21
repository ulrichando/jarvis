"""Write-denylist for the voice-agent's direct file tools (write/edit).

Threat model (CLAUDE.md): text reaching the supervisor LLM through the
user's mic can call write()/edit() on any path the 'ulrich' user can
write. The bash tool is already bwrap-sandboxed + secret-masked, but the
direct write/edit tools had NO path guard — a single injected
`write("~/.ssh/authorized_keys", ...)`, an edit of `~/.bashrc`, a systemd
unit, or anything under `~/.claude` would mean persistence or privilege
escalation. This module is that guard.

It refuses writes to:
  - SSH material + private keys (~/.ssh/**)
  - cloud / package / VCS credentials (~/.aws, ~/.gnupg, ~/.kube, ~/.docker,
    ~/.azure, ~/.config/gh, .netrc, .pgpass, .npmrc, .pypirc, .gitconfig)
  - shell-init files (~/.bashrc, ~/.zshrc, ~/.profile, …) — persistence
  - systemd units (~/.config/systemd/**, /etc/systemd/**, /etc/sudoers.d) —
    persistence
  - ~/.claude/** — Claude Code hooks/settings = escalation vector
  - JARVIS secrets: ~/.jarvis/local-api-token.env + the voice-agent's .env
  - /etc/sudoers, /etc/passwd, /etc/shadow

Symlink-safe: every path is resolved with os.path.realpath before the
check, so a benign-looking symlink that points into a denied tree is still
refused.

Optional confinement: if JARVIS_WRITE_SAFE_ROOT is set, ALL writes are
restricted to that subtree (off by default).

Ported/adapted from hermes/agent/file_safety.py (2026-05-20). Read-of-
secrets denial is intentionally a separate follow-up — this cut is
write/edit only.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

# The voice-agent's own .env (holds DEEPGRAM_API_KEY etc.), resolved
# relative to this module so the guard holds regardless of cwd.
_VOICE_AGENT_ENV = str((Path(__file__).resolve().parents[1] / ".env"))


def build_write_denied_paths(home: str) -> set[str]:
    """Exact sensitive files that must never be written."""
    return {
        os.path.realpath(p)
        for p in [
            os.path.join(home, ".ssh", "authorized_keys"),
            os.path.join(home, ".ssh", "id_rsa"),
            os.path.join(home, ".ssh", "id_ed25519"),
            os.path.join(home, ".ssh", "id_ecdsa"),
            os.path.join(home, ".ssh", "config"),
            os.path.join(home, ".bashrc"),
            os.path.join(home, ".zshrc"),
            os.path.join(home, ".zshenv"),
            os.path.join(home, ".profile"),
            os.path.join(home, ".bash_profile"),
            os.path.join(home, ".zprofile"),
            os.path.join(home, ".gitconfig"),
            os.path.join(home, ".netrc"),
            os.path.join(home, ".pgpass"),
            os.path.join(home, ".npmrc"),
            os.path.join(home, ".pypirc"),
            os.path.join(home, ".jarvis", "local-api-token.env"),
            _VOICE_AGENT_ENV,
            "/etc/sudoers",
            "/etc/passwd",
            "/etc/shadow",
        ]
    }


def build_write_denied_prefixes(home: str) -> list[str]:
    """Sensitive directory trees that must never be written into."""
    return [
        os.path.realpath(p) + os.sep
        for p in [
            os.path.join(home, ".ssh"),
            os.path.join(home, ".aws"),
            os.path.join(home, ".gnupg"),
            os.path.join(home, ".kube"),
            os.path.join(home, ".docker"),
            os.path.join(home, ".azure"),
            os.path.join(home, ".config", "gh"),
            os.path.join(home, ".config", "systemd"),  # user units → persistence
            os.path.join(home, ".claude"),              # hooks/settings → escalation
            "/etc/sudoers.d",
            "/etc/systemd",
        ]
    ]


def get_safe_write_root() -> Optional[str]:
    """Return the resolved JARVIS_WRITE_SAFE_ROOT, or None if unset."""
    root = os.getenv("JARVIS_WRITE_SAFE_ROOT", "")
    if not root:
        return None
    try:
        return os.path.realpath(os.path.expanduser(root))
    except Exception:
        return None


def is_write_denied(path: str) -> bool:
    """True if writing `path` is blocked by the denylist or safe-root."""
    if not path:
        return False
    home = os.path.realpath(os.path.expanduser("~"))
    resolved = os.path.realpath(os.path.expanduser(str(path)))

    if resolved in build_write_denied_paths(home):
        return True
    for prefix in build_write_denied_prefixes(home):
        if resolved.startswith(prefix):
            return True

    safe_root = get_safe_write_root()
    if safe_root and not (
        resolved == safe_root or resolved.startswith(safe_root + os.sep)
    ):
        return True

    return False


def write_denial_message(path: str) -> Optional[str]:
    """Return an 'Error: ...' string if writing `path` is denied, else None.

    Shaped for the supervisor LLM: it states the refusal is a
    non-retryable safety guard so the model doesn't loop retrying.
    """
    if is_write_denied(path):
        return (
            f"Error: refusing to write {path} — it is a protected/sensitive "
            f"location (credentials, SSH keys, shell-init, systemd units, "
            f"~/.claude, or a secret .env). This is a non-retryable safety "
            f"guard against prompt-injected writes, not a transient failure."
        )
    return None


# ── Read-of-secrets denial (exfiltration guard) ───────────────────────
#
# Narrower than the write denylist on purpose: reading a shell-init file
# or .gitconfig is not a secret leak (so they're write-denied but not
# read-denied). This list targets only credential/secret-bearing files —
# the threat is an injected supervisor reading them into the conversation
# and speaking them aloud.


def build_read_denied_paths(home: str) -> set[str]:
    """Exact secret-bearing files that must never be read."""
    return {
        os.path.realpath(p)
        for p in [
            os.path.join(home, ".netrc"),
            os.path.join(home, ".pgpass"),
            os.path.join(home, ".npmrc"),
            os.path.join(home, ".pypirc"),
            os.path.join(home, ".git-credentials"),
            os.path.join(home, ".jarvis", "local-api-token.env"),
            _VOICE_AGENT_ENV,
            "/etc/shadow",
        ]
    }


def build_read_denied_prefixes(home: str) -> list[str]:
    """Secret-bearing directory trees that must never be read."""
    return [
        os.path.realpath(p) + os.sep
        for p in [
            os.path.join(home, ".ssh"),
            os.path.join(home, ".aws"),
            os.path.join(home, ".gnupg"),
            os.path.join(home, ".kube"),
            os.path.join(home, ".docker"),
            os.path.join(home, ".azure"),
            os.path.join(home, ".config", "gh"),
        ]
    ]


def is_read_denied(path: str) -> bool:
    """True if reading `path` would expose a secret/credential file."""
    if not path:
        return False
    home = os.path.realpath(os.path.expanduser("~"))
    resolved = os.path.realpath(os.path.expanduser(str(path)))

    if resolved in build_read_denied_paths(home):
        return True
    for prefix in build_read_denied_prefixes(home):
        if resolved.startswith(prefix):
            return True

    return False


def read_denial_message(path: str) -> Optional[str]:
    """Return an 'Error: ...' string if reading `path` is denied, else None.

    Shaped for the supervisor LLM so it doesn't loop retrying.
    """
    if is_read_denied(path):
        return (
            f"Error: refusing to read {path} — it holds credentials/secrets "
            f"(SSH or cloud keys, auth tokens, or a secret .env). Reading it "
            f"into the conversation risks exfiltration via voice. This is a "
            f"non-retryable safety guard, not a transient failure."
        )
    return None
