"""Tests for scripts/check-windows-footguns.py.

The checker is a grep-based regression catcher for Windows cross-platform
footguns. It guards the Phase 2 voice-agent refactor (CLAUDE.md +
docs/superpowers/specs/2026-05-23-windows-install-phase1-design.md) by
flagging new Linux-only call sites that would crash on Windows.

These tests confirm the contract:
  - Each documented pattern is detected.
  - The ``# windows-footgun: ok`` inline suppression marker silences a match.
  - Same-line guard hints (hasattr/getattr/platform.system) suppress noise.
  - The CLI surface (--help, --list, --all) works without exploding.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

# Repo root is two levels above src/voice-agent/tests/.
REPO_ROOT = Path(__file__).resolve().parents[3]
CHECKER_PATH = REPO_ROOT / "scripts" / "check-windows-footguns.py"


# Load the script as a Python module so we can call functions directly
# (it has a hyphen in its name so a regular import won't work). The
# module must be registered in sys.modules BEFORE exec_module so that
# Python 3.13's stricter dataclass annotation resolver (which calls
# sys.modules.get(cls.__module__) to look up forward-ref types in the
# class's defining module) finds it. Without that, the @dataclass on
# Footgun raises AttributeError on the Optional[Callable] field.
def _load_checker():
    spec = importlib.util.spec_from_file_location(
        "check_windows_footguns", CHECKER_PATH
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_windows_footguns"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def checker():
    if not CHECKER_PATH.exists():
        pytest.skip(f"{CHECKER_PATH} not present")
    return _load_checker()


# ── Existence + smoke ────────────────────────────────────────────────────


def test_checker_file_exists():
    """The script ships at the documented path."""
    assert CHECKER_PATH.exists(), f"Expected {CHECKER_PATH} to exist"
    assert CHECKER_PATH.stat().st_size > 0


def test_help_runs(checker):
    """--help exits 0 and prints the usage banner."""
    # argparse reflows help text to the inherited terminal width (COLUMNS),
    # and textwrap's default break_on_hyphens can split INSIDE
    # "windows-footgun:" (e.g. COLUMNS=88 → "windows-\nfootgun: ok"), which
    # no whitespace normalization can rejoin. Caught live by the Stop
    # hook's environment, twice — first as a newline wrap, then as a
    # hyphen break after the flatten-only fix. Pin a wide COLUMNS so the
    # subprocess never wraps, and keep the flatten as belt-and-suspenders.
    result = subprocess.run(
        [sys.executable, str(CHECKER_PATH), "--help"],
        capture_output=True,
        text=True,
        env={**os.environ, "COLUMNS": "200"},
    )
    assert result.returncode == 0
    help_flat = " ".join(result.stdout.split())
    assert "Windows cross-platform footguns" in help_flat
    assert "windows-footgun: ok" in help_flat


def test_list_runs(checker):
    """--list prints all rules and exits 0."""
    result = subprocess.run(
        [sys.executable, str(CHECKER_PATH), "--list"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    # We expect at least the half-dozen JARVIS-specific rules.
    for required_rule in (
        "xdotool subprocess invocation",
        "pw-dump (PipeWire-only)",
        "setsid invocation",
        "systemctl --user",
        "hardcoded /tmp/jarvis-* path",
        "hardcoded ~/.jarvis path",
        "direct import fcntl",
        "direct import sdnotify",
    ):
        assert required_rule in result.stdout, (
            f"--list output missing {required_rule!r} — checker shipped without "
            "the JARVIS-specific rule set."
        )


# ── Detection (positive cases) ───────────────────────────────────────────


def _write_tmp_py(tmp_path: Path, body: str) -> Path:
    """Drop body into a .py file under tmp_path that the checker accepts.

    The checker insists files live UNDER REPO_ROOT (via relative_to) so we
    create a sibling dir inside scripts/ that we delete after the test.
    Cheaper than monkeypatching REPO_ROOT and avoids reloading the module.
    """
    # The checker scans relative_to(REPO_ROOT). Put the test file at
    # REPO_ROOT/scripts/_footgun_test_<unique>.py for the duration of
    # the test, then delete it. Using pytest's tmp_path doesn't work
    # because that's outside REPO_ROOT.
    fpath = REPO_ROOT / "scripts" / f"_footgun_test_{tmp_path.name}.py"
    fpath.write_text(body, encoding="utf-8")
    return fpath


def _run_checker(target: Path) -> tuple[int, str, str]:
    """Run the checker against ``target`` (file path)."""
    result = subprocess.run(
        [sys.executable, str(CHECKER_PATH), str(target)],
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout, result.stderr


@pytest.fixture
def in_repo_py(tmp_path):
    """Yield a Path inside REPO_ROOT/scripts that the checker will scan,
    and clean it up afterwards. Use as: ``in_repo_py("contents")`` -> Path.
    """
    created = []

    def _make(body: str) -> Path:
        target = _write_tmp_py(tmp_path, body)
        created.append(target)
        return target

    yield _make

    for f in created:
        try:
            f.unlink()
        except FileNotFoundError:
            pass


def test_detects_os_setsid(in_repo_py):
    target = in_repo_py("import os\nos.setsid()\n")
    code, out, _ = _run_checker(target)
    assert code == 1, "expected detection (exit 1)"
    assert "bare os.setsid" in out
    assert target.name in out


def test_detects_direct_import_fcntl(in_repo_py):
    """A real top-of-line `import fcntl` is flagged; a comment mention is not."""
    target = in_repo_py("import fcntl\n# import fcntl is only a comment\n")
    code, out, _ = _run_checker(target)
    assert code == 1, "expected detection (exit 1)"
    assert "direct import fcntl" in out
    assert f"{target.name}:2" not in out  # the comment line must NOT be flagged


def test_detects_direct_import_sdnotify(in_repo_py):
    """`from sdnotify import ...` is flagged → use pipeline.notify."""
    target = in_repo_py("from sdnotify import SystemdNotifier\n")
    code, out, _ = _run_checker(target)
    assert code == 1, "expected detection (exit 1)"
    assert "direct import sdnotify" in out


def test_detects_os_killpg(in_repo_py):
    target = in_repo_py("import os, signal\nos.killpg(1, signal.SIGTERM)\n")
    code, out, _ = _run_checker(target)
    assert code == 1
    assert "bare os.killpg" in out


def test_detects_sigkill(in_repo_py):
    target = in_repo_py("import signal\nx = signal.SIGKILL\n")
    code, out, _ = _run_checker(target)
    assert code == 1
    assert "bare signal.SIGKILL" in out


def test_detects_xdotool(in_repo_py):
    target = in_repo_py(
        "import subprocess\nsubprocess.run(['xdotool', 'key', 'Return'])\n"
    )
    code, out, _ = _run_checker(target)
    assert code == 1
    assert "xdotool subprocess invocation" in out


def test_detects_pw_dump(in_repo_py):
    target = in_repo_py(
        "import subprocess\nsubprocess.check_output(['pw-dump'])\n"
    )
    code, out, _ = _run_checker(target)
    assert code == 1
    assert "pw-dump" in out


def test_detects_setsid(in_repo_py):
    target = in_repo_py(
        'cmd = "setsid -f /usr/bin/firefox http://example.com"\n'
    )
    code, out, _ = _run_checker(target)
    assert code == 1
    assert "setsid invocation" in out


def test_detects_systemctl_user(in_repo_py):
    target = in_repo_py(
        "import subprocess\n"
        "subprocess.run(['systemctl', '--user', 'restart', 'jarvis-voice-agent.service'])\n"
    )
    code, out, _ = _run_checker(target)
    assert code == 1
    assert "systemctl --user" in out


def test_detects_tmp_jarvis_path(in_repo_py):
    target = in_repo_py('HEARTBEAT = "/tmp/jarvis-worker-heartbeat"\n')
    code, out, _ = _run_checker(target)
    assert code == 1
    assert "hardcoded /tmp/jarvis-* path" in out


def test_detects_hardcoded_jarvis_home(in_repo_py):
    target = in_repo_py('db = "~/.jarvis/conversations.db"\n')
    code, out, _ = _run_checker(target)
    assert code == 1
    assert "hardcoded ~/.jarvis path" in out


def test_detects_hardcoded_data_home(in_repo_py):
    target = in_repo_py('logs = "~/.local/share/jarvis/logs"\n')
    code, out, _ = _run_checker(target)
    assert code == 1
    assert "hardcoded ~/.local/share/jarvis path" in out


def test_detects_open_without_encoding(in_repo_py):
    target = in_repo_py("open('/etc/hosts')\n")
    code, out, _ = _run_checker(target)
    assert code == 1
    assert "open() without an explicit encoding" in out


# ── Suppression ──────────────────────────────────────────────────────────


def test_suppress_marker_silences_match(in_repo_py):
    """`# windows-footgun: ok` on the same line silences detection."""
    target = in_repo_py(
        "import os\n"
        "os.setsid()  # windows-footgun: ok -- only called on POSIX\n"
    )
    code, out, _ = _run_checker(target)
    assert code == 0, f"expected suppression to silence detection; got:\n{out}"


def test_hasattr_guard_silences_match(in_repo_py):
    """Same-line hasattr(os, 'setsid') hints suppress the os.setsid rule."""
    target = in_repo_py(
        "import os\n"
        "if hasattr(os, 'setsid'): os.setsid()\n"
    )
    code, out, _ = _run_checker(target)
    assert code == 0, f"expected hasattr() guard to suppress; got:\n{out}"


def test_getattr_guard_silences_sigkill(in_repo_py):
    """getattr(signal, 'SIGKILL', ...) suppresses the SIGKILL rule."""
    target = in_repo_py(
        "import signal\n"
        "sig = getattr(signal, 'SIGKILL', signal.SIGTERM)\n"
    )
    code, out, _ = _run_checker(target)
    assert code == 0, f"expected getattr() guard to suppress; got:\n{out}"


def test_platform_check_silences_match(in_repo_py):
    """if platform.system() != 'Windows' suppresses the line's footgun."""
    target = in_repo_py(
        "import os, platform\n"
        "if platform.system() != 'Windows': os.setsid()\n"
    )
    code, out, _ = _run_checker(target)
    assert code == 0, f"expected platform-check to suppress; got:\n{out}"


def test_open_with_encoding_does_not_fire(in_repo_py):
    target = in_repo_py("open('/etc/hosts', 'r', encoding='utf-8')\n")
    code, out, _ = _run_checker(target)
    assert code == 0, f"expected encoding= to suppress; got:\n{out}"


def test_open_binary_mode_does_not_fire(in_repo_py):
    target = in_repo_py("open('/tmp/data.bin', 'rb')\n")
    code, out, _ = _run_checker(target)
    assert code == 0


# ── Multiple matches in one file ─────────────────────────────────────────


def test_detects_multiple_footguns(in_repo_py):
    body = textwrap.dedent("""\
        import os, signal, subprocess
        os.setsid()
        x = signal.SIGKILL
        subprocess.run(['xdotool', 'key', 'F1'])
    """)
    target = in_repo_py(body)
    code, out, _ = _run_checker(target)
    assert code == 1
    assert "bare os.setsid" in out
    assert "bare signal.SIGKILL" in out
    assert "xdotool" in out
