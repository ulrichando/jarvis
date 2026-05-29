"""Tests for the foundational shell + file tools (registry wave).

Proves:
  (a) All tool modules self-register: registry.all_entries() includes
      terminal, read_file, write_file, patch, search_files.
  (b) load_all_livekit_tools() yields valid RawFunctionTool objects for each.
  (c) Behavior smoke tests:
      - terminal: echo command runs and returns expected output.
      - write_file -> read_file: round-trip in a tmp dir.
      - patch: replace mode edits a file in place.
      - search_files: finds content in a tmp file.
      - ansi_strip: cleans ANSI codes.
  (d) Acceptance gate: tool implementation files contain zero forbidden tokens
      (checked by test_no_forbidden_tokens_in_tool_files below).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Make the voice-agent root importable, mirroring other test modules.
_VOICE_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_VOICE_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_VOICE_AGENT_ROOT))


def _run(coro):
    """Run a coroutine to completion on a throwaway event loop."""
    return asyncio.new_event_loop().run_until_complete(coro)


def _invoke(tool, args: dict):
    """Invoke a RawFunctionTool the way the framework does."""
    return _run(tool(raw_arguments=args))


# ── Force module import so self-registration runs ──────────────────────────

import tools.terminal_tool  # noqa: F401, E402
import tools.file_tools     # noqa: F401, E402

from tools.registry import registry  # noqa: E402
from tools._adapter import load_all_livekit_tools, to_livekit_tool  # noqa: E402
from livekit.agents.llm import is_raw_function_tool  # noqa: E402


# ── (a) Self-registration checks ───────────────────────────────────────────

_EXPECTED_TOOL_NAMES = {"terminal", "read_file", "write_file", "patch", "search_files"}


def test_all_foundational_tools_registered():
    """All five foundational tools must appear in the registry."""
    registered_names = {e.name for e in registry.all_entries()}
    assert _EXPECTED_TOOL_NAMES.issubset(registered_names), (
        f"Missing tools: {_EXPECTED_TOOL_NAMES - registered_names}"
    )


def test_terminal_tool_registered_with_correct_toolset():
    entry = registry.get_entry("terminal")
    assert entry is not None
    assert entry.toolset == "terminal"
    assert entry.handler is not None
    assert entry.is_async is False


def test_file_tools_registered_with_correct_toolset():
    for name in ("read_file", "write_file", "patch", "search_files"):
        entry = registry.get_entry(name)
        assert entry is not None, f"Missing entry: {name}"
        assert entry.toolset == "file", f"{name}: wrong toolset {entry.toolset!r}"
        assert entry.handler is not None


# ── (b) load_all_livekit_tools adapts foundational tools ──────────────────

def test_load_all_livekit_tools_yields_raw_function_tools():
    tools = load_all_livekit_tools()
    assert all(is_raw_function_tool(t) for t in tools)
    names = {t.info.name for t in tools}
    assert _EXPECTED_TOOL_NAMES.issubset(names), (
        f"Missing adapted tools: {_EXPECTED_TOOL_NAMES - names}"
    )


def test_adapted_tools_have_valid_schemas():
    """Every adapted tool must carry a non-empty description and parameters."""
    tools = load_all_livekit_tools()
    for t in tools:
        if t.info.name not in _EXPECTED_TOOL_NAMES:
            continue
        rs = t.info.raw_schema
        assert rs.get("description"), f"{t.info.name}: empty description"
        params = rs.get("parameters", {})
        assert params.get("type") == "object", f"{t.info.name}: parameters.type != object"


# ── (c) Behavior smoke tests ────────────────────────────────────────────────


class TestTerminalTool:
    def test_echo_runs_and_returns_output(self):
        from tools.terminal_tool import terminal_tool
        raw = terminal_tool("echo hello-jarvis")
        result = json.loads(raw)
        assert result["exit_code"] == 0, result
        assert "hello-jarvis" in result["output"]

    def test_nonexistent_command_returns_nonzero(self):
        from tools.terminal_tool import terminal_tool
        raw = terminal_tool("this-command-does-not-exist-jarvis-test")
        result = json.loads(raw)
        assert result["exit_code"] != 0

    def test_exit_code_meaning_for_grep_no_match(self):
        from tools.terminal_tool import terminal_tool
        raw = terminal_tool("grep jarvis-no-match /dev/null")
        result = json.loads(raw)
        assert result["exit_code"] == 1
        assert "exit_code_meaning" in result
        assert "No matches" in result["exit_code_meaning"]

    def test_invalid_command_type_returns_error(self):
        from tools.terminal_tool import terminal_tool
        raw = terminal_tool(None)  # type: ignore[arg-type]
        result = json.loads(raw)
        assert result["exit_code"] == -1
        assert "Invalid command" in result["error"]

    def test_long_lived_foreground_blocked(self):
        from tools.terminal_tool import terminal_tool
        raw = terminal_tool("npm run dev")
        result = json.loads(raw)
        assert result["exit_code"] == -1
        assert "background" in result["error"].lower()

    def test_workdir_is_honored(self, tmp_path):
        from tools.terminal_tool import terminal_tool
        raw = terminal_tool("pwd", workdir=str(tmp_path))
        result = json.loads(raw)
        assert result["exit_code"] == 0
        assert str(tmp_path) in result["output"]

    def test_dangerous_workdir_blocked(self):
        from tools.terminal_tool import terminal_tool
        raw = terminal_tool("pwd", workdir="/tmp; rm -rf /")
        result = json.loads(raw)
        assert result["exit_code"] == -1
        assert "disallowed" in result["error"].lower() or "Blocked" in result["error"]

    def test_livekit_adapted_terminal_runs(self):
        tools = load_all_livekit_tools()
        terminal = next(t for t in tools if t.info.name == "terminal")
        result = _invoke(terminal, {"command": "echo adapted-ok"})
        data = json.loads(result)
        assert data["exit_code"] == 0
        assert "adapted-ok" in data["output"]


class TestFileRoundTrip:
    def test_write_then_read_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
        from tools.file_tools import _write_file_impl, _read_file_impl, _read_tracker
        # Clear tracker to avoid cross-test contamination.
        _read_tracker.clear()

        filepath = str(tmp_path / "hello.txt")
        content = "Hello JARVIS\nLine two\n"

        wr = json.loads(_write_file_impl(filepath, content))
        assert wr["success"] is True
        assert wr["bytes_written"] == len(content.encode("utf-8"))

        rr = json.loads(_read_file_impl(filepath))
        assert "Hello JARVIS" in rr["content"]
        assert rr["total_lines"] == 2

    def test_write_creates_parent_dirs(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
        from tools.file_tools import _write_file_impl, _read_tracker
        _read_tracker.clear()

        filepath = str(tmp_path / "a" / "b" / "c.txt")
        wr = json.loads(_write_file_impl(filepath, "nested"))
        assert wr["success"] is True
        assert Path(filepath).exists()

    def test_read_with_offset_and_limit(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
        from tools.file_tools import _write_file_impl, _read_file_impl, _read_tracker
        _read_tracker.clear()

        filepath = str(tmp_path / "lines.txt")
        lines = "\n".join(str(i) for i in range(1, 11)) + "\n"
        _write_file_impl(filepath, lines)

        rr = json.loads(_read_file_impl(filepath, offset=3, limit=3))
        assert rr["offset"] == 3
        assert rr["limit"] == 3
        # Lines 3, 4, 5 should be in content (1-indexed, so "3", "4", "5")
        assert "3\t3" in rr["content"]  # "LINE\tCONTENT"
        assert "4\t4" in rr["content"]
        assert "5\t5" in rr["content"]

    def test_read_dedup_returns_stub_on_second_identical_read(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
        from tools.file_tools import _write_file_impl, _read_file_impl, _read_tracker
        _read_tracker.clear()

        filepath = str(tmp_path / "dedup.txt")
        _write_file_impl(filepath, "stable content")

        r1 = json.loads(_read_file_impl(filepath, session_id="dedup-sess"))
        assert "content" in r1  # first read returns content

        r2 = json.loads(_read_file_impl(filepath, session_id="dedup-sess"))
        # Second read of unchanged file → dedup stub
        assert r2.get("dedup") is True or "unchanged" in r2.get("status", "")

    def test_write_sensitive_path_rejected(self, tmp_path):
        from tools.file_tools import _write_file_impl
        raw = _write_file_impl("/etc/passwd", "evil")
        result = json.loads(raw)
        assert "error" in result
        assert "sensitive" in result["error"].lower() or "Refusing" in result["error"]

    def test_write_rejects_internal_status_text(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
        from tools.file_tools import _write_file_impl, _READ_DEDUP_STATUS_MESSAGE
        filepath = str(tmp_path / "bad.txt")
        raw = _write_file_impl(filepath, _READ_DEDUP_STATUS_MESSAGE)
        result = json.loads(raw)
        assert "error" in result

    def test_read_binary_extension_blocked(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
        from tools.file_tools import _read_file_impl
        filepath = str(tmp_path / "image.png")
        Path(filepath).write_bytes(b"\x89PNG")
        raw = _read_file_impl(filepath)
        result = json.loads(raw)
        assert "error" in result
        assert "binary" in result["error"].lower()

    def test_read_device_path_blocked(self):
        from tools.file_tools import _read_file_impl
        raw = _read_file_impl("/dev/zero")
        result = json.loads(raw)
        assert "error" in result
        assert "device" in result["error"].lower()


class TestPatchTool:
    def test_replace_mode_edits_in_place(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
        from tools.file_tools import _write_file_impl, _patch_impl, _read_file_impl, _read_tracker
        _read_tracker.clear()

        filepath = str(tmp_path / "edit.txt")
        _write_file_impl(filepath, "hello world\n")

        pr = json.loads(_patch_impl(
            mode="replace",
            path=filepath,
            old_string="hello world",
            new_string="hello JARVIS",
        ))
        assert pr["success"] is True
        assert pr["replacements"] == 1
        assert "diff" in pr

        rr = json.loads(_read_file_impl(filepath))
        assert "hello JARVIS" in rr["content"]

    def test_replace_mode_fails_on_missing_old_string(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
        from tools.file_tools import _write_file_impl, _patch_impl, _read_tracker
        _read_tracker.clear()

        filepath = str(tmp_path / "no_match.txt")
        _write_file_impl(filepath, "line one\n")
        pr = json.loads(_patch_impl(
            mode="replace",
            path=filepath,
            old_string="NOT IN FILE",
            new_string="replacement",
        ))
        assert "error" in pr
        assert "Could not find" in pr["error"]

    def test_replace_all_mode(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
        from tools.file_tools import _write_file_impl, _patch_impl, _read_file_impl, _read_tracker
        _read_tracker.clear()

        filepath = str(tmp_path / "multi.txt")
        _write_file_impl(filepath, "foo foo foo\n")

        pr = json.loads(_patch_impl(
            mode="replace",
            path=filepath,
            old_string="foo",
            new_string="bar",
            replace_all=True,
        ))
        assert pr["success"] is True
        assert pr["replacements"] == 3

        rr = json.loads(_read_file_impl(filepath))
        assert "bar bar bar" in rr["content"]

    def test_missing_path_returns_error(self):
        from tools.file_tools import _patch_impl
        pr = json.loads(_patch_impl(mode="replace", path=None, old_string="x", new_string="y"))
        assert "error" in pr

    def test_duplicate_match_rejected_without_replace_all(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
        from tools.file_tools import _write_file_impl, _patch_impl, _read_tracker
        _read_tracker.clear()

        filepath = str(tmp_path / "dup.txt")
        _write_file_impl(filepath, "cat\ncat\n")
        pr = json.loads(_patch_impl(
            mode="replace", path=filepath, old_string="cat", new_string="dog"
        ))
        assert "error" in pr
        assert "2" in pr["error"]  # mentions occurrences count

    def test_livekit_adapted_patch_runs(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
        from tools.file_tools import _write_file_impl, _read_tracker
        _read_tracker.clear()

        filepath = str(tmp_path / "adapted.txt")
        _write_file_impl(filepath, "old-value\n")

        tools = load_all_livekit_tools()
        patch_tool = next(t for t in tools if t.info.name == "patch")
        result = json.loads(_invoke(patch_tool, {
            "mode": "replace",
            "path": filepath,
            "old_string": "old-value",
            "new_string": "new-value",
        }))
        assert result.get("success") is True


class TestSearchFiles:
    def test_content_search_finds_pattern(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
        from tools.file_tools import _write_file_impl, _search_files_impl, _read_tracker
        _read_tracker.clear()

        filepath = str(tmp_path / "target.txt")
        _write_file_impl(filepath, "JARVIS_SECRET_TOKEN=abc123\nother line\n")

        sr = json.loads(_search_files_impl("JARVIS_SECRET_TOKEN", path=str(tmp_path)))
        assert sr.get("total_matches", 0) >= 1 or len(sr.get("matches", [])) >= 1

    def test_file_search_by_glob(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
        from tools.file_tools import _write_file_impl, _search_files_impl, _read_tracker
        _read_tracker.clear()

        (tmp_path / "a.py").write_text("x=1")
        (tmp_path / "b.txt").write_text("y=2")

        sr = json.loads(_search_files_impl("*.py", target="files", path=str(tmp_path)))
        files = sr.get("files", [])
        assert any("a.py" in f for f in files)
        assert not any("b.txt" in f for f in files)

    def test_loop_detection_blocks_after_4_identical_searches(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
        from tools.file_tools import _search_files_impl, _read_tracker
        _read_tracker.clear()

        for _ in range(3):
            _search_files_impl("unique-pattern-xyz", path=str(tmp_path), session_id="loop-test-sess")

        r4 = json.loads(_search_files_impl("unique-pattern-xyz", path=str(tmp_path), session_id="loop-test-sess"))
        assert "error" in r4
        assert "BLOCKED" in r4["error"]


class TestAnsiStrip:
    def test_strips_color_codes(self):
        from tools.ansi_strip import strip_ansi
        colored = "\x1b[31mred text\x1b[0m"
        assert strip_ansi(colored) == "red text"

    def test_clean_text_passes_through(self):
        from tools.ansi_strip import strip_ansi
        plain = "no escape codes here"
        assert strip_ansi(plain) is plain or strip_ansi(plain) == plain

    def test_empty_string(self):
        from tools.ansi_strip import strip_ansi
        assert strip_ansi("") == ""

    def test_strips_csi_sequence(self):
        from tools.ansi_strip import strip_ansi
        assert strip_ansi("\x1b[2J") == ""

    def test_strips_osc_sequence(self):
        from tools.ansi_strip import strip_ansi
        assert strip_ansi("\x1b]0;title\x07") == ""


# ── Acceptance gate: implementation files contain no forbidden upstream tokens ──

def test_no_forbidden_tokens_in_tool_files():
    """Tool implementation files must contain zero upstream-namespace tokens.

    The forbidden token is checked as a word boundary so the test function
    itself (which necessarily discusses the token) doesn't self-match.
    We only scan implementation files, not this test file.
    """
    import subprocess
    impl_files = [
        _VOICE_AGENT_ROOT / "tools" / "terminal_tool.py",
        _VOICE_AGENT_ROOT / "tools" / "file_tools.py",
        _VOICE_AGENT_ROOT / "tools" / "ansi_strip.py",
    ]
    # The forbidden token is the upstream project namespace.
    # We use -w for whole-word match: "sha1sum" of "upstream-namespace" is
    # "h-e-r-m-e-s" (5 chars). Checked case-insensitively.
    forbidden = "hermes"
    for f in impl_files:
        assert f.exists(), f"Expected implementation file missing: {f}"
        result = subprocess.run(
            ["grep", "-rin", "-w", forbidden, str(f)],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0 and result.stdout.strip() == "", (
            f"Forbidden token '{forbidden}' found in {f}:\n{result.stdout}"
        )
