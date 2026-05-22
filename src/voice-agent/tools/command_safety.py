"""Catastrophic-command safety scanner for the JARVIS voice-agent terminal tool.

Threat model (CLAUDE.md): mic / prompt-injection can make the agent run
anything as the local user. The terminal tool's existing workdir validation
only checks the *working directory*, not the *command content*. This module
closes that gap for genuinely destructive commands.

Design philosophy — this is a CATASTROPHIC-COMMAND SAFETY NET, not a nanny.
Block only patterns that would cause irreversible system-wide damage or exfil
real secrets over a network. When in doubt, ALLOW. Over-blocking legit dev
commands (rm -rf ./build, pip install, git ...) would break real workflows.

Patterns blocked:
  1. Fork bomb:        :(){ :|:& };: and close variants.
  2. rm -rf on root/home/system paths:
       Targets: /, /*, ~, ~/, $HOME, /etc, /usr, /bin, /sbin, /boot,
                /var, /lib, /sys, /proc, /dev, bare * at root.
       Allowed: relative paths, /tmp/..., specific subdirs under the target.
  3. Disk / FS destruction:
       mkfs*, dd ... of=/dev/sd|nvme|disk|hd, > /dev/sd..., wipefs,
       shred /dev/...
  4. Pipe network-to-interpreter:
       curl|wget ... | (sudo )? sh|bash|zsh|fish|python|perl|ruby|node
  5. Recursive perms on root / system paths:
       chmod -R 777 /      (root/system target ONLY, not ./)
       chown -R ... /
  6. Secret EXFIL: reading a known secret path AND sending to a network sink
       in the same command. Reading alone = ALLOW. Read + curl/wget/nc = block.

Env bypass: JARVIS_TERMINAL_UNRESTRICTED=1 → always return None (power-user
opt-out). Document before using.
"""
from __future__ import annotations

import os
import re
from typing import Optional

# ---------------------------------------------------------------------------
# Env bypass
# ---------------------------------------------------------------------------

def _is_unrestricted() -> bool:
    """Return True when the power-user bypass is active."""
    return os.getenv("JARVIS_TERMINAL_UNRESTRICTED", "0").strip() == "1"


# ---------------------------------------------------------------------------
# Pattern library — compile once at module import
# ---------------------------------------------------------------------------

# 1. Fork bomb — classic zsh/bash shape :(){ :|:& };: and variants.
#    Match any colon-named function that self-references with pipe + & + }.
_FORK_BOMB_RE = re.compile(
    r":\s*\(\s*\)\s*\{"   # :(){
    r".*?:\s*\|",          # ... : |
    re.DOTALL,
)

# 2a. rm -rf / rm -fr / rm --recursive --force flags (order-independent).
#     We extract the flags and then check the target path separately.
_RM_RE = re.compile(
    r"\brm\b"
    r"(?:[^;&|`\n]*?(?:-[^\s]*[rR][^\s]*f[^\s]*|-[^\s]*f[^\s]*[rR][^\s]*"
    r"|--recursive\b|--force\b|--no-preserve-root\b)){1,6}"
    r"[^;&|`\n]*",
    re.IGNORECASE,
)

# System/root paths that must not be wiped.  Bare * is included.
# Deliberately excludes /tmp and relative paths (legit devtools).
_RM_DANGEROUS_TARGETS_RE = re.compile(
    r"""(?x)
    (?:^|\s)        # must be preceded by whitespace or start
    (
      /\*?(?=\s|$)        # bare /  or  /* — only when end or whitespace follows
      | ~/?\*?(?=\s|$)    # ~  ~/  ~/*  — bare tilde, end/whitespace must follow
      | \$HOME/?\*?(?=\s|$) # $HOME  or  $HOME/*
      | /etc(?=\s|$|/)
      | /usr(?=\s|$|/)
      | /bin(?=\s|$|/)
      | /sbin(?=\s|$|/)
      | /boot(?=\s|$|/)
      | /var(?=\s|$|/)
      | /lib(?=\s|$|/)
      | /sys(?=\s|$|/)
      | /proc(?=\s|$|/)
      | /dev(?=\s|$|/)
    )
    """,
)

# 3. Disk / FS destruction.
_MKFS_RE = re.compile(r"\bmkfs(?:\.[a-z0-9]+)?\b", re.IGNORECASE)

# dd output to physical block devices.
_DD_DEVICE_RE = re.compile(
    r"\bdd\b.*?\bof=/dev/(?:sd[a-z]|nvme|disk|hd[a-z])",
    re.DOTALL | re.IGNORECASE,
)

# Redirect to physical block devices (>  /dev/sda etc.)
_REDIRECT_DEVICE_RE = re.compile(
    r">\s*/dev/(?:sd[a-z]|nvme|disk|hd[a-z])",
    re.IGNORECASE,
)

_WIPEFS_RE = re.compile(r"\bwipefs\b", re.IGNORECASE)

_SHRED_DEVICE_RE = re.compile(
    r"\bshred\b[^;&|`\n]*/dev/(?:sd[a-z]|nvme|disk|hd[a-z])",
    re.IGNORECASE | re.DOTALL,
)

# 4. Pipe network-to-interpreter (curl|wget ... | sh|bash|etc.).
#    Optional sudo before the interpreter.
_PIPE_EXEC_RE = re.compile(
    r"(?:curl|wget)\b[^;&|`\n]*\|[^;&|`\n]*(?:sudo\s+)?(?:sh|bash|zsh|fish|python3?|perl|ruby|node)\b",
    re.IGNORECASE | re.DOTALL,
)

# 5. Recursive perms on root/system paths.
_CHMOD_ROOT_RE = re.compile(
    r"\bchmod\b[^;&|`\n]*-[^\s]*R[^\s]*[^;&|`\n]*"
    r"(?:777|000|a\+rwx)[^;&|`\n]*"
    r"(?:/\s*$|/\s*&&|\s+/$|\s+/\s)",
    re.IGNORECASE,
)
_CHOWN_ROOT_RE = re.compile(
    r"\bchown\b[^;&|`\n]*-[^\s]*R[^\s]*[^;&|`\n]*"
    r"(?:/\s*$|/\s*&&|\s+/$|\s+/\s)",
    re.IGNORECASE,
)

# 6. Secret EXFIL: read a secret path AND pipe/send to a network tool.
#
# "Read" is broad: cat, <, $(<), base64, xxd, openssl, head, tail, grep,
# cp when the src is a secret.  We look for secret path patterns + a
# network sink (curl/wget/nc/netcat/ncat/socat) in the same single-pipe
# expression (i.e. before the first ;, &&, or ||).
#
# Reading alone is ALLOWED — only the combined read+network shape is blocked.

_SECRET_PATH_RE = re.compile(
    r"""(?x)
    (
      ~/\.ssh/id         # SSH private key prefix
      | \.aws/credentials
      | \.netrc\b
      | /etc/shadow\b
      | [A-Z_]+_API_KEY  # env-style API key names literally in the command
      | _TOKEN\b
      | \.env\b
    )
    """,
)

_NETWORK_SINK_RE = re.compile(
    r"\b(?:curl|wget|nc|netcat|ncat|socat)\b",
    re.IGNORECASE,
)


def _rm_is_catastrophic(command: str) -> bool:
    """True if the command contains rm -rf targeting a root/system path."""
    # Find each rm ... segment in the command.
    for m in _RM_RE.finditer(command):
        segment = m.group(0)
        if _RM_DANGEROUS_TARGETS_RE.search(segment):
            return True
    return False


def _exfil_is_catastrophic(command: str) -> bool:
    """True when the command reads a secret path AND sends it over the network."""
    if not _SECRET_PATH_RE.search(command):
        return False
    if not _NETWORK_SINK_RE.search(command):
        return False
    return True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_BYPASS_HINT = (
    "If you are certain this command is safe, set JARVIS_TERMINAL_UNRESTRICTED=1 "
    "in the voice-agent environment and restart the agent to override this guard."
)


def scan_command(command: str) -> Optional[str]:
    """Scan *command* for catastrophic patterns.

    Returns a denial string if the command is blocked, else None (allow).
    The denial string is shaped for the supervisor LLM: it explains the
    refusal and makes clear it is a non-retryable safety guard.

    Environment bypass: set ``JARVIS_TERMINAL_UNRESTRICTED=1`` to always
    return None regardless of the pattern match.  This is for power-user
    / advanced-dev scenarios where the user explicitly wants unrestricted
    terminal access.  Document the flag when enabling it.
    """
    if _is_unrestricted():
        return None

    if not command or not command.strip():
        return None

    # -- 1. Fork bomb --
    if _FORK_BOMB_RE.search(command):
        return (
            "Error: refusing to run command — it matches a fork-bomb pattern "
            "(:(){ :|:& };: or a variant). This is a non-retryable safety guard. "
            + _BYPASS_HINT
        )

    # -- 2. rm -rf on root/system paths --
    if _rm_is_catastrophic(command):
        return (
            "Error: refusing to run command — it contains `rm -rf` (or variant) "
            "targeting a root or system path (/, ~, /etc, /usr, /bin, /var, etc.). "
            "Relative paths and /tmp/ targets are allowed; this block applies only "
            "to system-wide destruction targets. This is a non-retryable safety guard. "
            + _BYPASS_HINT
        )

    # -- 3. Disk / FS destruction --
    if _MKFS_RE.search(command):
        return (
            "Error: refusing to run command — it invokes `mkfs` which would "
            "format a filesystem/device. This is a non-retryable safety guard. "
            + _BYPASS_HINT
        )
    if _DD_DEVICE_RE.search(command):
        return (
            "Error: refusing to run command — it uses `dd` with output to a "
            "physical block device (of=/dev/sd*, nvme, etc.), which would "
            "destroy disk data. This is a non-retryable safety guard. "
            + _BYPASS_HINT
        )
    if _REDIRECT_DEVICE_RE.search(command):
        return (
            "Error: refusing to run command — it redirects output directly to "
            "a physical block device (/dev/sd*, nvme, etc.), which would "
            "destroy disk data. This is a non-retryable safety guard. "
            + _BYPASS_HINT
        )
    if _WIPEFS_RE.search(command):
        return (
            "Error: refusing to run command — it invokes `wipefs` which destroys "
            "filesystem signatures on a device. This is a non-retryable safety guard. "
            + _BYPASS_HINT
        )
    if _SHRED_DEVICE_RE.search(command):
        return (
            "Error: refusing to run command — it uses `shred` on a physical "
            "block device, which would permanently destroy device data. "
            "This is a non-retryable safety guard. "
            + _BYPASS_HINT
        )

    # -- 4. Pipe network-to-interpreter --
    if _PIPE_EXEC_RE.search(command):
        return (
            "Error: refusing to run command — it pipes a network download "
            "(curl/wget) directly into a shell or interpreter, which is a "
            "classic remote code execution vector. Fetch first, inspect, then "
            "run explicitly. This is a non-retryable safety guard. "
            + _BYPASS_HINT
        )

    # -- 5. Recursive perms on root/system paths --
    if _CHMOD_ROOT_RE.search(command):
        return (
            "Error: refusing to run command — it applies recursive `chmod` on "
            "a root or system path, which would break system permissions. "
            "This is a non-retryable safety guard. "
            + _BYPASS_HINT
        )
    if _CHOWN_ROOT_RE.search(command):
        return (
            "Error: refusing to run command — it applies recursive `chown` on "
            "a root or system path. This is a non-retryable safety guard. "
            + _BYPASS_HINT
        )

    # -- 6. Secret EXFIL --
    if _exfil_is_catastrophic(command):
        return (
            "Error: refusing to run command — it reads a known secret / credential "
            "path and simultaneously sends the result to a network tool (curl/wget/nc). "
            "Reading local files is allowed; sending secrets over the network is not. "
            "This is a non-retryable safety guard against credential exfiltration. "
            + _BYPASS_HINT
        )

    return None
