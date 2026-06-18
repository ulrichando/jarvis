#!/usr/bin/env python3
"""
Grep-based checker for Windows cross-platform footguns in JARVIS.

JARVIS today is a Linux-first voice agent (CLAUDE.md: PipeWire, systemd
--user, xdotool, /tmp/jarvis-*, ~/.jarvis paths). The Phase 1 Windows
installer (install.ps1) ships CLI + Desktop UI but defers the voice
agent's service install because the agent imports Linux-only modules.
Phase 2 will refactor those behind platform-abstraction layers — this
script is the regression catcher that keeps the gap from re-opening
as new code lands.

It runs as a fast grep pass:
  - Flags POSIX-only stdlib (os.setsid / os.fork / signal.SIGKILL / ...)
  - Flags JARVIS-specific Linux footguns (xdotool, pw-dump, wpctl,
    setsid, systemctl --user, /tmp/jarvis-*, hardcoded ~/.jarvis paths)
  - Honours an inline ``# windows-footgun: ok`` suppression marker on
    the same line for intentional platform-gated code
  - Skips lines that already look guarded (hasattr(os, ...), platform.
    system(), shutil.which(...), IS_WINDOWS sentinel)

Usage:
    # Scan staged changes (default when run from a git checkout)
    python scripts/check-windows-footguns.py

    # Scan the full tree (full-repo audit)
    python scripts/check-windows-footguns.py --all

    # Scan a specific file or directory
    python scripts/check-windows-footguns.py path/to/file.py path/to/dir/

    # Scan only files changed vs. master
    python scripts/check-windows-footguns.py --diff master

    # Print the rule list and exit
    python scripts/check-windows-footguns.py --list

Exit status:
    0 — no Windows footguns found (or all matches suppressed)
    1 — at least one unsuppressed match

Suppress an intentional use (tests, platform-gated code) with:
    os.kill(pid, 0)  # windows-footgun: ok — only called on POSIX
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent

SUPPRESS_MARKER = re.compile(r"#\s*windows-footgun\s*:\s*ok\b", re.IGNORECASE)

# Line-level guard hints. If a line contains any of these tokens, we assume
# the programmer wrote the line in full awareness of the Windows pitfall —
# e.g. ``if hasattr(os, 'setsid'): ... os.setsid()``, or the classic
# ``getattr(signal, 'SIGKILL', signal.SIGTERM)``, or ``shutil.which('xdotool')``.
# False negatives are fine here — the inline ``# windows-footgun: ok``
# marker is the authoritative suppression. This is just to keep the noise
# floor low on obviously-guarded lines.
GUARD_HINTS = (
    "hasattr(os,",
    "hasattr(signal,",
    "getattr(os,",
    "getattr(signal,",
    "shutil.which(",
    'if platform.system() != "Windows"',
    "if platform.system() != 'Windows'",
    'if platform.system() == "Linux"',
    "if platform.system() == 'Linux'",
    'if sys.platform == "win32"',
    'if sys.platform != "win32"',
    "if sys.platform == 'win32'",
    "if sys.platform != 'win32'",
    "if sys.platform.startswith('linux')",
    'if sys.platform.startswith("linux")',
    "IS_WINDOWS",
    "is_windows",
    "IS_LINUX",
    "is_linux",
    "JARVIS_PLATFORM",
)

# Dirs we never scan.
EXCLUDED_DIRS = {
    ".git",
    "node_modules",
    "venv",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    "site-packages",
    "target",            # Cargo build artifacts (Tauri)
    ".next",             # Next.js build artifacts (Web)
    "out",               # Various build outputs
    # Sibling reference checkouts (kept as study material) are scoped out
    # by virtue of --all only walking the explicit JARVIS-owned roots
    # (src/voice-agent, src/hub, scripts, bin) -- no per-name allowlist
    # needed here.
}

# File globs we never scan (beyond the dirs above).
EXCLUDED_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".so",
    ".dll",
    ".exe",
    ".png",
    ".jpg",
    ".gif",
    ".ico",
    ".svg",
    ".mp4",
    ".mp3",
    ".wav",
    ".pdf",
    ".zip",
    ".tar",
    ".gz",
    ".whl",
    ".lock",
    ".min.js",
    ".min.css",
}

# Files we never scan — self-referential (this script lists the patterns
# it detects) plus docs / specs that necessarily mention them by name.
EXCLUDED_FILES = {
    "scripts/check-windows-footguns.py",
    "src/voice-agent/tests/test_windows_footguns_checker.py",
    # Asserts the Linux backend of tools.desktop_control invokes xdotool
    # with the expected argv — every flagged ``["xdotool", ...]`` literal
    # in this file is a fixture/assertion, not a real shellout.
    "src/voice-agent/tests/test_desktop_control.py",
    "docs/superpowers/specs/2026-05-23-windows-install-phase1-design.md",
    "CONTRIBUTING.md",
    "CLAUDE.md",
}


@dataclass
class Footgun:
    """A Windows cross-platform footgun pattern."""

    name: str
    pattern: re.Pattern
    message: str
    fix: str
    # If set, matches in files/paths containing any of these substrings are
    # silently ignored (e.g. tests that legitimately exercise the footgun
    # behind a platform guard). Prefer ``# windows-footgun: ok`` inline
    # suppression over this list; only use path_allowlist for whole files
    # that are inherently tests of the footgun itself.
    path_allowlist: tuple[str, ...] = ()
    # Optional post-match predicate. Takes the re.Match and the full line,
    # returns True if the match is a REAL footgun (not a false positive).
    # Use when the regex can't fully distinguish — e.g. open() where mode
    # may contain "b" for binary or the line may have encoding= elsewhere.
    # Typed as Optional[Callable] (Optional[...] form, not X|None) because
    # the dataclass decorator's annotation resolver under importlib spec-
    # loaded modules trips over PEP 604 union types — the failing path is
    # dataclasses._is_type -> sys.modules.get(cls.__module__) which can
    # return None when the module is imported via importlib.util (the
    # test_windows_footguns_checker test harness does this).
    post_filter: Optional[Callable] = None


# ── JARVIS-specific footguns (the bits the Linux installer hides) ────────


def _is_xdotool_subprocess(match: "re.Match", line: str) -> bool:
    """True if the match looks like a real xdotool subprocess invocation.

    The token "xdotool" appears in plenty of comments / docstrings
    documenting the Linux side-channel. The footgun is when it's actually
    invoked. Heuristic: require an adjacent subprocess. call or list-of-args
    context.
    """
    # Drop pure mentions inside string literals not in an argv-shape list.
    if "subprocess" in line or 'shell=True' in line:
        return True
    # ``[..., 'xdotool', ...]`` is an argv list.
    if re.search(r"""\[\s*['"]xdotool['"]""", line):
        return True
    # ``check_output('xdotool ...')`` / ``Popen('xdotool ...')``
    if re.search(r"""\b(?:check_output|check_call|run|Popen|call)\s*\(\s*['"]xdotool\b""", line):
        return True
    return False


def _is_direct_import(*modules: str) -> Callable:
    """post_filter factory: match only a real top-of-line ``import <mod>`` /
    ``from <mod> import`` statement, skipping comments + docstring mentions."""
    prefixes = tuple(p for m in modules for p in (f"import {m}", f"from {m} ", f"from {m}."))

    def _filter(match: "re.Match", line: str) -> bool:
        return line.strip().startswith(prefixes)

    return _filter


FOOTGUNS: list[Footgun] = [
    # ── Linux-only stdlib that import-time crashes on Windows ───────────
    Footgun(
        name="bare os.setsid",
        pattern=re.compile(r"(?<!hasattr\()(?<!getattr\()\bos\.setsid\b"),
        message=(
            "os.setsid does not exist on Windows and raises "
            "AttributeError. Subprocesses that need detachment on "
            "Windows use creationflags instead."
        ),
        fix=(
            "if platform.system() != 'Windows':\n"
            "    kwargs['preexec_fn'] = os.setsid\n"
            "else:\n"
            "    kwargs['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP"
        ),
    ),
    Footgun(
        name="bare os.killpg",
        pattern=re.compile(r"\bos\.killpg\b"),
        message="os.killpg does not exist on Windows.",
        fix=(
            "Use psutil for cross-platform process-tree kill:\n"
            "  p = psutil.Process(pid)\n"
            "  for c in p.children(recursive=True): c.kill()\n"
            "  p.kill()"
        ),
    ),
    Footgun(
        name="bare os.fork",
        pattern=re.compile(r"(?<!hasattr\()\bos\.fork\s*\("),
        message="os.fork does not exist on Windows.",
        fix=(
            "Use subprocess.Popen for daemonization, or guard with "
            "hasattr(os, 'fork') and a Windows fallback path."
        ),
    ),
    Footgun(
        name="bare os.getuid / os.geteuid / os.getgid",
        pattern=re.compile(r"\bos\.(?:getuid|geteuid|getgid|getegid)\b"),
        message=(
            "os.getuid / os.geteuid / os.getgid do not exist on Windows "
            "and raise AttributeError at import time if referenced."
        ),
        fix=(
            "Use getpass.getuser() for the username, or gate with "
            "hasattr(os, 'getuid')."
        ),
    ),
    Footgun(
        name="os.kill(pid, 0)",
        pattern=re.compile(r"\bos\.kill\s*\(\s*[^,]+,\s*0\s*\)"),
        message=(
            "os.kill(pid, 0) is NOT a no-op on Windows -- it sends "
            "CTRL_C_EVENT to the target's console process group, "
            "hard-killing the target and potentially unrelated siblings. "
            "See bpo-14484."
        ),
        fix=(
            "Use psutil.pid_exists(pid). The voice-agent has psutil as a "
            "core dependency (requirements.txt)."
        ),
    ),
    Footgun(
        name="bare signal.SIGKILL",
        pattern=re.compile(r"\bsignal\.SIGKILL\b"),
        message=(
            "signal.SIGKILL does not exist on Windows and raises "
            "AttributeError at import time."
        ),
        fix="Use getattr(signal, 'SIGKILL', signal.SIGTERM).",
    ),
    Footgun(
        name="bare signal.SIGHUP / SIGUSR1 / SIGUSR2 / SIGALRM / SIGCHLD / SIGPIPE / SIGQUIT",
        pattern=re.compile(
            r"\bsignal\.(?:SIGHUP|SIGUSR1|SIGUSR2|SIGALRM|SIGCHLD|SIGPIPE|SIGQUIT)\b"
        ),
        message=(
            "These POSIX signals don't exist on Windows; referencing "
            "them raises AttributeError at import time."
        ),
        fix=(
            "Use getattr(signal, 'SIGXXX', None) and check for None "
            "before using, or gate the whole block behind a platform check."
        ),
    ),
    Footgun(
        name="asyncio add_signal_handler without try/except",
        pattern=re.compile(r"\.add_signal_handler\s*\("),
        message=(
            "loop.add_signal_handler raises NotImplementedError on "
            "Windows -- always wrap in try/except or gate with a "
            "platform check."
        ),
        fix=(
            "try:\n"
            "    loop.add_signal_handler(sig, handler, sig)\n"
            "except NotImplementedError:\n"
            "    pass  # Windows asyncio doesn't support signal handlers"
        ),
    ),
    # ── open() encoding (text-mode default differs across platforms) ────
    Footgun(
        name="open() without encoding= on text mode",
        # Match builtins.open() specifically -- NOT os.open(), .open()
        # method calls (Path.open, tarfile.open, zf.open, webbrowser.open,
        # Image.open, wave.open, etc), or ``async def open()`` method
        # definitions. The pattern requires a start-of-identifier boundary
        # before ``open(`` so ``os.open``, ``.open``, ``def open`` are all
        # skipped.
        pattern=re.compile(
            r"""(?:^|[\s\(,;=])(?<![.\w])open\s*\(\s*[^,)]+\s*(?:,\s*['"](?P<mode>[^'"]*)['"])?"""
        ),
        message=(
            "open() without an explicit encoding= uses the platform default "
            "(UTF-8 on POSIX, cp1252/mbcs on Windows) -- files round-tripped "
            "between hosts get mojibake. Always pass encoding='utf-8' for "
            "text files, or use open(path, 'rb')/'wb' for binary."
        ),
        fix=(
            "open(path, 'r', encoding='utf-8')  # or 'utf-8-sig' if the "
            "file may have a BOM"
        ),
        post_filter=lambda m, line: (
            "b" not in (m.group("mode") or "")
            and "encoding=" not in line
            and "encoding =" not in line
            # Skip ``def open(`` and ``async def open(`` method definitions.
            and not line.lstrip().startswith("def ")
            and not line.lstrip().startswith("async def ")
            # Skip ``open(path, **kwargs)`` -- encoding may be in the dict.
            and "**" not in line
        ),
    ),
    # ── subprocess pitfalls ──────────────────────────────────────────────
    Footgun(
        name="subprocess shebang script invocation",
        pattern=re.compile(
            r"subprocess\.(?:run|Popen|call|check_output|check_call)\s*\(\s*\[\s*['\"]\./"
        ),
        message=(
            "Running a script via './scriptname' doesn't work on Windows -- "
            "shebang lines aren't honoured. CreateProcessW can't execute "
            "bash/python scripts without an explicit interpreter."
        ),
        fix="Use [sys.executable, 'scriptname.py', ...] explicitly.",
    ),
    Footgun(
        name="wmic invocation without shutil.which guard",
        pattern=re.compile(
            r"""(?:subprocess\.\w+\s*\(\s*\[\s*['"]wmic['"]|['"]wmic\.exe['"])"""
        ),
        message=(
            "wmic was removed in Windows 10 21H1 and later. Always "
            "gate with shutil.which('wmic') and fall back to "
            "PowerShell (Get-CimInstance Win32_Process)."
        ),
        fix=(
            "if shutil.which('wmic'):\n"
            "    ... wmic path ...\n"
            "else:\n"
            "    subprocess.run(['powershell', '-NoProfile', '-Command',\n"
            "                    'Get-CimInstance Win32_Process | ...'])"
        ),
    ),
    # ── JARVIS-specific Linux-only call sites ───────────────────────────
    Footgun(
        name="xdotool subprocess invocation",
        # xdotool is the X11 input automation tool used by computer_use.
        # On Windows we'd use pyautogui or pygetwindow / pywinauto.
        pattern=re.compile(r"""(?:['"]xdotool['"]|\bxdotool\s+(?:search|key|type|windowactivate))"""),
        message=(
            "xdotool is X11-only and not available on Windows or macOS. "
            "computer_use's input layer must abstract this behind a "
            "platform-aware helper (pyautogui on Windows, ydotool/wlrctl "
            "on Wayland)."
        ),
        fix=(
            "Wrap the call:\n"
            "  if shutil.which('xdotool'):\n"
            "      subprocess.run(['xdotool', ...])\n"
            "  else:\n"
            "      pyautogui.click(...) / pyautogui.typewrite(...)"
        ),
        post_filter=_is_xdotool_subprocess,
    ),
    Footgun(
        name="pw-dump (PipeWire-only)",
        pattern=re.compile(r"""(?:['"]pw-dump['"]|\bpw-dump\b)"""),
        message=(
            "pw-dump is a PipeWire CLI; Windows has no PipeWire. The "
            "audio-health probes must short-circuit on non-Linux."
        ),
        fix=(
            "if platform.system() == 'Linux' and shutil.which('pw-dump'):\n"
            "    ... pw-dump path ...\n"
            "else:\n"
            "    return {}  # no audio-health data outside Linux/PipeWire"
        ),
    ),
    Footgun(
        name="wpctl (WirePlumber CLI, PipeWire-only)",
        pattern=re.compile(r"""(?:['"]wpctl['"]|\bwpctl\s+(?:status|set-default|set-volume))"""),
        message=(
            "wpctl is the WirePlumber CLI; Windows has no WirePlumber. "
            "Mirror the pw-dump gate -- short-circuit on non-Linux."
        ),
        fix=(
            "if platform.system() == 'Linux' and shutil.which('wpctl'):\n"
            "    subprocess.run(['wpctl', ...])"
        ),
    ),
    Footgun(
        name="pactl (PulseAudio CLI, Linux-only)",
        pattern=re.compile(r"""(?:['"]pactl['"]|\bpactl\s+(?:list|info|set-sink))"""),
        message=(
            "pactl is PulseAudio's CLI; Windows uses WASAPI. The "
            "voice-agent already prefers pw-dump (CLAUDE.md: PipeWire-"
            "native box, NO pactl); any new pactl reference is a "
            "regression."
        ),
        fix=(
            "Use pw-dump (already gated) for audio inspection on Linux. "
            "On Windows, query WASAPI via pyaudio / sounddevice."
        ),
    ),
    Footgun(
        name="setsid invocation",
        pattern=re.compile(r"""(?:['"]setsid['"]|\bsetsid\s+(?:-f\s+)?\S+)"""),
        message=(
            "setsid is a Linux util-linux command used to launch a new "
            "process in its own session. Windows has no equivalent in "
            "PATH; use Start-Process / CREATE_NEW_PROCESS_GROUP."
        ),
        fix=(
            "On Linux:  subprocess.Popen(['setsid', '-f', cmd, ...])\n"
            "On Windows: subprocess.Popen(cmd, creationflags=subprocess."
            "CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS)"
        ),
    ),
    Footgun(
        name="systemctl --user (Linux-only service control)",
        pattern=re.compile(r"""(?:['"]systemctl['"][^\)]*--user|systemctl\s+--user\b)"""),
        message=(
            "systemctl is a Linux systemd CLI. Windows uses Task Scheduler "
            "or Services. Service-control code must be platform-abstracted."
        ),
        fix=(
            "Wrap behind a platform check or a service-control helper:\n"
            "  if platform.system() == 'Linux':\n"
            "      subprocess.run(['systemctl', '--user', 'restart', svc])\n"
            "  elif platform.system() == 'Windows':\n"
            "      # schtasks /Run /TN ... or sc.exe restart ..."
        ),
    ),
    Footgun(
        name="hardcoded /tmp/jarvis-* path",
        # Heuristic: literal /tmp/jarvis-... in a string. Plenty of
        # JARVIS code currently uses /tmp/jarvis-worker-heartbeat,
        # /tmp/jarvis-launch-*, etc. -- all need to move to a
        # cross-platform tmp path via tempfile.gettempdir() or
        # tools.runtime.get_jarvis_home() before Phase 2.
        pattern=re.compile(r"""['"]/tmp/jarvis[-_][^'"\s]+['"]"""),
        message=(
            "/tmp/jarvis-* is a Linux-only path. Windows has no /tmp; "
            "use tempfile.gettempdir() / Path(tempfile.gettempdir()) or "
            "tools.runtime.get_jarvis_home() (honours JARVIS_HOME on "
            "every platform)."
        ),
        fix=(
            "import tempfile\n"
            "heartbeat = Path(tempfile.gettempdir()) / 'jarvis-worker-heartbeat'\n"
            "# or for persistent state:\n"
            "from tools.runtime import get_jarvis_home\n"
            "heartbeat = get_jarvis_home() / 'worker-heartbeat'"
        ),
    ),
    Footgun(
        name="hardcoded ~/.jarvis path (string literal)",
        # Hardcoded "~/.jarvis/..." or '~/.jarvis/...' literal in code
        # bypasses tools.runtime.get_jarvis_home(), which honours
        # JARVIS_HOME. Tests / docs that mention the path are fine
        # (this only flags literals inside Python expressions that
        # look like they'll be used as paths).
        pattern=re.compile(r"""['"]~/\.jarvis(?:/[^'"\n]*)?['"]"""),
        message=(
            "Hardcoded '~/.jarvis/...' bypasses tools.runtime.get_jarvis_home(), "
            "which honours the JARVIS_HOME env var (set by install.ps1 on "
            "Windows + tests' isolation fixtures). On Windows the canonical "
            "user-data dir is %USERPROFILE%\\.jarvis, which expanduser will "
            "resolve correctly -- but only if the call goes through the "
            "helper."
        ),
        fix=(
            "from tools.runtime import get_jarvis_home\n"
            "memories = get_jarvis_home() / 'memories'\n"
            "# or for read-only references inside docstrings:\n"
            "# add ``# windows-footgun: ok`` to suppress the check"
        ),
    ),
    Footgun(
        name="hardcoded ~/.local/share/jarvis path",
        # XDG_DATA_HOME on Linux. Windows equivalent is %LOCALAPPDATA%\jarvis\data.
        pattern=re.compile(r"""['"]~/\.local/share/jarvis(?:/[^'"\n]*)?['"]"""),
        message=(
            "'~/.local/share/jarvis/...' is the Linux XDG_DATA_HOME default. "
            "On Windows the equivalent is %LOCALAPPDATA%\\jarvis\\data\\. Both "
            "should resolve via a cross-platform get_jarvis_data_home() "
            "helper instead of hardcoded strings."
        ),
        fix=(
            "Use xdg-base-dirs / platformdirs.user_data_dir('jarvis') for "
            "cross-platform resolution, OR add a get_jarvis_data_home() "
            "helper next to get_jarvis_home() in tools.runtime."
        ),
    ),
    Footgun(
        name="hardcoded ~/Desktop (OneDrive trap)",
        pattern=re.compile(
            r"""['"](?:~|~/|[A-Z]:[/\\]Users[/\\][^/\\'"]+[/\\])Desktop\b"""
        ),
        message=(
            "When OneDrive Backup is enabled on Windows, the real Desktop "
            "is at %USERPROFILE%\\OneDrive\\Desktop, not %USERPROFILE%\\"
            "Desktop (which exists as an empty husk)."
        ),
        fix=(
            "On Windows, resolve via ctypes + SHGetKnownFolderPath, or "
            "run PowerShell [Environment]::GetFolderPath('Desktop'), or "
            "use platformdirs.user_desktop_dir()."
        ),
    ),
    # ── Linux-only modules whose direct import crashes module-load on Windows ──
    Footgun(
        name="direct import fcntl (use pipeline.portable_lock)",
        # Broad token match; the post_filter narrows to real import statements so
        # comments/docstrings mentioning fcntl don't false-match.
        pattern=re.compile(r"\bfcntl\b"),
        message=(
            "fcntl is a Unix-only stdlib module — a direct `import fcntl` "
            "hard-ImportErrors on Windows (it broke voice-agent startup via "
            "cron_delivery/cron_scheduler before this rule existed). File "
            "locking must go through pipeline.portable_lock, which dispatches "
            "to fcntl on POSIX and msvcrt on Windows."
        ),
        fix=(
            "from pipeline import portable_lock\n"
            "with portable_lock.exclusive_lock(fileobj):  # or lock_exclusive/unlock\n"
            "    ...\n"
            "(portable_lock.py is the ONE allowed home for `import fcntl`, "
            "suppressed inline with `# windows-footgun: ok`.)"
        ),
        post_filter=_is_direct_import("fcntl"),
    ),
    Footgun(
        name="direct import sdnotify (use pipeline.notify)",
        pattern=re.compile(r"\bsdnotify\b"),
        message=(
            "sdnotify speaks the systemd notify protocol (Linux+systemd only). "
            "A direct import couples the voice-agent to systemd and is dead "
            "weight on Windows/macOS. Use pipeline.notify."
        ),
        fix=(
            "from pipeline import notify\n"
            "notifier = notify.get_notifier()  # real sdnotify on Linux, no-op elsewhere\n"
            "(notify.py is the ONE allowed home for `import sdnotify`, "
            "suppressed inline with `# windows-footgun: ok`.)"
        ),
        post_filter=_is_direct_import("sdnotify"),
    ),
]


def should_scan_file(path: Path) -> bool:
    """Return True if this file is in scope for the checker."""
    # Skip files outside the repo root entirely.
    try:
        rel = path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return False
    # Skip the excluded dirs.
    parts = set(path.parts)
    if parts & EXCLUDED_DIRS:
        return False
    # Skip excluded suffixes.
    for suffix in EXCLUDED_SUFFIXES:
        if str(path).endswith(suffix):
            return False
    # Skip self + docs that intentionally mention the patterns.
    if rel in EXCLUDED_FILES:
        return False
    # Only scan Python files (the rules are Python-centric).
    if path.suffix in {".py", ".pyw", ".pyi"}:
        return True
    return False


def iter_files(paths: Iterable[Path]) -> Iterable[Path]:
    for p in paths:
        if p.is_file():
            if should_scan_file(p):
                yield p
        elif p.is_dir():
            for root, dirs, files in os.walk(p):
                # Prune excluded dirs in-place for speed.
                dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]
                for fname in files:
                    fpath = Path(root) / fname
                    if should_scan_file(fpath):
                        yield fpath


def _strip_code(line: str) -> str:
    """Return just the code portion of a line — strip trailing comments and
    skip lines that are entirely inside a string literal or comment.

    Heuristic (we don't parse Python); good enough to avoid flagging our
    own docstring-style examples that mention the pattern by name.
    """
    stripped = line.lstrip()
    if stripped.startswith("#"):
        return ""
    hash_idx = _find_unquoted_hash(line)
    if hash_idx is not None:
        return line[:hash_idx]
    return line


def _find_unquoted_hash(line: str) -> int | None:
    """Index of the first ``#`` not inside a single/double-quoted string.

    Simple state machine — good enough for the 99% case of "code, then
    optional trailing comment."
    """
    i = 0
    n = len(line)
    in_s = False  # single-quote string
    in_d = False  # double-quote string
    while i < n:
        c = line[i]
        if c == "\\" and (in_s or in_d) and i + 1 < n:
            i += 2
            continue
        if not in_d and c == "'":
            in_s = not in_s
        elif not in_s and c == '"':
            in_d = not in_d
        elif c == "#" and not in_s and not in_d:
            return i
        i += 1
    return None


def scan_file(path: Path, footguns: list[Footgun]) -> list[tuple[int, str, Footgun]]:
    """Return a list of (line_number, line, footgun) for unsuppressed matches."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    matches: list[tuple[int, str, Footgun]] = []

    # Track whether we're inside a triple-quoted string. Simple state
    # machine — handles both ''' and """, toggled by the FIRST triple
    # we see; we don't try to handle nested or f-string cases.
    in_triple: str | None = None

    for i, line in enumerate(text.splitlines(), start=1):
        code_for_scan = line
        if in_triple:
            if in_triple in line:
                after = line.split(in_triple, 1)[1]
                in_triple = None
                code_for_scan = after
            else:
                continue
        for delim in ('"""', "'''"):
            if delim in code_for_scan:
                count = code_for_scan.count(delim)
                if count % 2 == 1:
                    before = code_for_scan.split(delim, 1)[0]
                    code_for_scan = before
                    in_triple = delim
                    break
                else:
                    parts = code_for_scan.split(delim)
                    code_for_scan = "".join(parts[::2])
                    break

        if SUPPRESS_MARKER.search(line):
            continue
        if any(hint in line for hint in GUARD_HINTS):
            continue
        code = _strip_code(code_for_scan)
        if not code.strip():
            continue
        for fg in footguns:
            if fg.path_allowlist and any(s in str(path) for s in fg.path_allowlist):
                continue
            match = fg.pattern.search(code)
            if not match:
                continue
            if fg.post_filter is not None:
                try:
                    if not fg.post_filter(match, line):
                        continue
                except (IndexError, AttributeError):
                    # Post-filter assumed a named group that isn't there — skip.
                    continue
            matches.append((i, line.rstrip(), fg))
    return matches


def get_staged_files() -> list[Path]:
    """Return paths staged in the current git index. Empty on non-git trees."""
    try:
        out = subprocess.check_output(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
            cwd=REPO_ROOT,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    return [REPO_ROOT / f for f in out.splitlines() if f.strip()]


def get_diff_files(ref: str) -> list[Path]:
    """Return paths modified vs. the given git ref."""
    try:
        out = subprocess.check_output(
            ["git", "diff", f"{ref}...HEAD", "--name-only", "--diff-filter=ACMR"],
            cwd=REPO_ROOT,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    return [REPO_ROOT / f for f in out.splitlines() if f.strip()]


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Flag Windows cross-platform footguns in JARVIS Python code. "
            "Backed by the # windows-footgun: ok inline suppression marker "
            "for intentional platform-gated uses."
        )
    )
    p.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Specific files/dirs to scan (default: staged changes).",
    )
    p.add_argument(
        "--all",
        action="store_true",
        help="Scan the full repository (src/voice-agent/, scripts/, bin/, ...).",
    )
    p.add_argument(
        "--diff",
        metavar="REF",
        help="Scan files changed vs. the given git ref (e.g. --diff master).",
    )
    p.add_argument(
        "--list",
        action="store_true",
        help="List all known footgun rules and exit.",
    )
    return p.parse_args(argv)


def print_rules() -> None:
    print("Known Windows footguns checked by this script:\n")
    for i, fg in enumerate(FOOTGUNS, start=1):
        print(f"{i:2}. {fg.name}")
        print(f"    {fg.message}")
        # First line of the fix; the full multi-line example is in code.
        print(f"    Fix: {fg.fix.splitlines()[0]}")
        print()


def main(argv: list[str]) -> int:
    # Windows terminals default to cp1252, which can't encode the check
    # marks etc. that we'd otherwise use. Reconfigure to UTF-8 so the
    # script works on the very platform it's designed to help.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    args = parse_args(argv)

    if args.list:
        print_rules()
        return 0

    if args.all:
        # Scan the JARVIS-owned Python trees. NOT src/cli/ (off-limits per
        # CLAUDE.md), NOT sibling reference checkouts (only the explicit
        # roots below are walked), NOT venvs / build artifacts (handled
        # by EXCLUDED_DIRS).
        roots = [
            REPO_ROOT / "src" / "voice-agent",
            REPO_ROOT / "src" / "hub",
            REPO_ROOT / "scripts",
            REPO_ROOT / "bin",
        ]
        roots = [r for r in roots if r.exists()]
    elif args.diff:
        roots = get_diff_files(args.diff)
    elif args.paths:
        roots = [p.resolve() for p in args.paths]
    else:
        roots = get_staged_files()
        if not roots:
            print(
                "No staged files to scan. Pass --all for a full-repo scan, "
                "--diff <ref> for a range diff, or paths explicitly.",
                file=sys.stderr,
            )
            return 0

    total_matches = 0
    files_scanned = 0
    for path in iter_files(roots):
        files_scanned += 1
        matches = scan_file(path, FOOTGUNS)
        for lineno, line, fg in matches:
            try:
                rel = path.relative_to(REPO_ROOT).as_posix()
            except ValueError:
                rel = str(path)
            print(f"{rel}:{lineno}: [{fg.name}]")
            print(f"    {line.strip()}")
            print(f"    -- {fg.message}")
            print(f"    Fix: {fg.fix.splitlines()[0]}")
            print()
            total_matches += 1

    if total_matches:
        print(
            f"\n[X] {total_matches} Windows footgun(s) found across "
            f"{files_scanned} file(s) scanned.",
            file=sys.stderr,
        )
        print(
            "    If an individual match is a false positive or intentionally "
            "platform-gated, suppress it with `# windows-footgun: ok` on "
            "the same line.\n    Run with --list to see all rules.",
            file=sys.stderr,
        )
        return 1

    print(
        f"[OK] No Windows footguns found ({files_scanned} file(s) scanned)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
