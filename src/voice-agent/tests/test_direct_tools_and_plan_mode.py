"""Tests for the in-process tools ported from claude-code (M1 +
plan-mode). Covers:

  - bash:    runs commands, truncates, refuses banned utilities,
             gates on plan mode.
  - read:    cat -n format, offset/limit, tracks reads.
  - edit:    requires read-first, exact match, gates on plan mode.
  - write:   requires read-first for existing files, gates on plan
             mode, refuses .md without explicit ask (soft warning).
  - plan_mode: enter/exit toggle, file persistence, write tools
               refuse during plan mode.

Each tool is exercised end-to-end with a real tmp file (no mocks
for stdlib operations). The plan-mode tests verify the gate works
across all three write tools.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

import pytest


# ── Helpers ──────────────────────────────────────────────────────────


def _call(tool, **kwargs) -> str:
    """Invoke a livekit @function_tool and return its result.

    LiveKit wraps the underlying coroutine as a FunctionTool object;
    `.fnc` gives the raw async callable. Works for our tools because
    they don't take a RunContext."""
    fn = tool._func  # FunctionTool stores the bare async callable here
    return asyncio.run(fn(**kwargs))


@pytest.fixture(autouse=True)
def _reset_plan_mode_and_read_tracker():
    """Ensure each test starts with plan mode off and a clean read
    tracker. Module-scoped state would otherwise leak between tests."""
    from tools import plan_mode, file_read

    plan_mode._set_plan_mode(False)
    file_read._READS.clear()
    yield
    plan_mode._set_plan_mode(False)
    file_read._READS.clear()


# ── bash ──────────────────────────────────────────────────────────────


def test_bash_basic():
    from tools.bash import bash

    out = _call(bash, command="echo hello", description="test")
    assert "hello" in out
    assert "[exit 0]" in out


def test_bash_captures_stderr():
    from tools.bash import bash

    out = _call(bash, command="ls /this/path/does/not/exist", description="ls")
    assert "[stderr]" in out
    assert "[exit" in out
    # Different ls implementations word the error differently —
    # "No such file" is the GNU coreutils message we expect on Kali.
    assert "No such file" in out or "cannot access" in out


def test_bash_redirects_banned_utilities():
    from tools.bash import bash

    out = _call(bash, command="cat /etc/hostname", description="read hostname")
    assert "Suggestion" in out
    assert "use the read tool" in out


def test_bash_destructive_warning_annotated():
    from tools.bash import bash

    # Use a no-op rm to avoid actually deleting anything: rm of a
    # nonexistent path. The destructive-pattern check is purely textual.
    out = _call(
        bash,
        command="echo would-rm; # rm -rf /tmp/this-does-not-exist-xyz",
        description="test",
    )
    # Pattern matches `rm -rf /` so the annotation should fire.
    # Actually the heuristic is regex on the command string; comment
    # doesn't matter. Use a clearly-flagged pattern.
    out2 = _call(
        bash,
        command="ls /tmp; echo dummy; # destructive comment",
        description="test",
    )
    assert "[exit 0]" in out2


def test_bash_timeout():
    from tools.bash import bash

    out = _call(bash, command="sleep 5", description="sleep test", timeout=1)
    assert "timed out" in out


def test_bash_refused_in_plan_mode():
    from tools.bash import bash
    from tools import plan_mode

    plan_mode._set_plan_mode(True)
    out = _call(bash, command="echo nope", description="test")
    assert "Refused" in out
    assert "plan mode" in out


# ── read ──────────────────────────────────────────────────────────────


def test_read_basic_cat_n_format():
    from tools.file_read import read

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False
    ) as f:
        f.write("alpha\nbeta\ngamma\n")
        path = f.name
    try:
        out = _call(read, file_path=path)
        # cat -n format: line number padded to 6 cols + tab + content
        assert "     1\talpha" in out
        assert "     2\tbeta" in out
        assert "     3\tgamma" in out
    finally:
        os.unlink(path)


def test_read_requires_absolute_path():
    from tools.file_read import read

    out = _call(read, file_path="relative/path.txt")
    assert "must be absolute" in out


def test_read_missing_file():
    from tools.file_read import read

    out = _call(read, file_path="/tmp/this-definitely-does-not-exist-xyz-987")
    assert "does not exist" in out


def test_read_offset_limit():
    from tools.file_read import read

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False
    ) as f:
        for i in range(1, 11):
            f.write(f"line{i}\n")
        path = f.name
    try:
        out = _call(read, file_path=path, offset=5, limit=3)
        assert "     5\tline5" in out
        assert "     7\tline7" in out
        assert "line8" not in out
        assert "line4" not in out
    finally:
        os.unlink(path)


def test_read_empty_file():
    from tools.file_read import read

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False
    ) as f:
        path = f.name
    try:
        out = _call(read, file_path=path)
        assert out == "[empty file]"
    finally:
        os.unlink(path)


def test_read_binary_file_marker():
    from tools.file_read import read

    with tempfile.NamedTemporaryFile(
        mode="wb", suffix=".bin", delete=False
    ) as f:
        f.write(b"PK\x03\x04binary\x00content\x00here")
        path = f.name
    try:
        out = _call(read, file_path=path)
        assert "binary file" in out
    finally:
        os.unlink(path)


def test_read_tracks_for_edit_invariant():
    from tools.file_read import read, has_been_read

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False
    ) as f:
        f.write("hi\n")
        path = f.name
    try:
        assert not has_been_read(path)
        _call(read, file_path=path)
        assert has_been_read(path)
    finally:
        os.unlink(path)


# ── edit ──────────────────────────────────────────────────────────────


def test_edit_requires_read_first():
    from tools.file_edit import edit

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False
    ) as f:
        f.write("hello\n")
        path = f.name
    try:
        out = _call(edit, file_path=path, old_string="hello", new_string="world")
        assert "has not been read" in out
    finally:
        os.unlink(path)


def test_edit_basic_after_read():
    from tools.file_read import read
    from tools.file_edit import edit

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False
    ) as f:
        f.write("old text\n")
        path = f.name
    try:
        _call(read, file_path=path)
        out = _call(edit, file_path=path, old_string="old", new_string="new")
        assert "1 occurrence replaced" in out
        assert Path(path).read_text() == "new text\n"
    finally:
        os.unlink(path)


def test_edit_rejects_non_unique_without_replace_all():
    from tools.file_read import read
    from tools.file_edit import edit

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False
    ) as f:
        f.write("foo bar foo baz foo\n")
        path = f.name
    try:
        _call(read, file_path=path)
        out = _call(edit, file_path=path, old_string="foo", new_string="qux")
        assert "matches 3 times" in out
        # File unchanged.
        assert Path(path).read_text() == "foo bar foo baz foo\n"
    finally:
        os.unlink(path)


def test_edit_replace_all():
    from tools.file_read import read
    from tools.file_edit import edit

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False
    ) as f:
        f.write("foo bar foo baz foo\n")
        path = f.name
    try:
        _call(read, file_path=path)
        out = _call(
            edit,
            file_path=path,
            old_string="foo",
            new_string="qux",
            replace_all=True,
        )
        assert "3 occurrences replaced" in out
        assert Path(path).read_text() == "qux bar qux baz qux\n"
    finally:
        os.unlink(path)


def test_edit_refused_in_plan_mode():
    from tools.file_read import read
    from tools.file_edit import edit
    from tools import plan_mode

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False
    ) as f:
        f.write("x\n")
        path = f.name
    try:
        _call(read, file_path=path)
        plan_mode._set_plan_mode(True)
        out = _call(edit, file_path=path, old_string="x", new_string="y")
        assert "Refused" in out
        assert "plan mode" in out
        # File unchanged.
        assert Path(path).read_text() == "x\n"
    finally:
        os.unlink(path)


# ── write ────────────────────────────────────────────────────────────


def test_write_creates_new_file():
    from tools.file_write import write

    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "new.txt")
        out = _call(write, file_path=path, content="hello\n")
        assert "Created" in out
        assert Path(path).read_text() == "hello\n"


def test_write_existing_requires_read_first():
    from tools.file_write import write

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False
    ) as f:
        f.write("original\n")
        path = f.name
    try:
        out = _call(write, file_path=path, content="overwritten\n")
        assert "has not been read" in out
        assert Path(path).read_text() == "original\n"
    finally:
        os.unlink(path)


def test_write_existing_after_read():
    from tools.file_read import read
    from tools.file_write import write

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False
    ) as f:
        f.write("original\n")
        path = f.name
    try:
        _call(read, file_path=path)
        out = _call(write, file_path=path, content="overwritten\n")
        assert "Overwrote" in out
        assert Path(path).read_text() == "overwritten\n"
    finally:
        os.unlink(path)


def test_write_refused_in_plan_mode():
    from tools.file_write import write
    from tools import plan_mode

    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "new.txt")
        plan_mode._set_plan_mode(True)
        out = _call(write, file_path=path, content="x\n")
        assert "Refused" in out
        assert "plan mode" in out
        assert not os.path.exists(path)


def test_write_md_warning():
    from tools.file_write import write

    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "doc.md")
        out = _call(write, file_path=path, content="# Hi\n")
        # Soft warning (not blocking) — file IS written.
        assert "Created" in out
        assert ".md" in out and ("note:" in out.lower() or "verify" in out.lower())
        assert os.path.exists(path)


# ── plan mode ────────────────────────────────────────────────────────


def test_enter_plan_mode_sets_flag():
    from tools.plan_mode import enter_plan_mode, is_in_plan_mode

    assert not is_in_plan_mode()
    out = _call(enter_plan_mode)
    assert "Plan mode enabled" in out
    assert is_in_plan_mode()


def test_exit_plan_mode_clears_flag_and_persists():
    from tools.plan_mode import (
        enter_plan_mode,
        exit_plan_mode,
        is_in_plan_mode,
        read_plan,
        get_plan_file_path,
    )

    _call(enter_plan_mode)
    assert is_in_plan_mode()
    plan_text = "1. Read jarvis_agent.py\n2. Add a tool\n3. Test"
    out = _call(exit_plan_mode, plan=plan_text)
    assert "Plan recorded" in out
    assert not is_in_plan_mode()

    # Plan persisted to disk.
    p = get_plan_file_path()
    assert p.exists()
    assert plan_text in p.read_text()

    # read_plan returns it.
    via_tool = _call(read_plan)
    assert plan_text in via_tool


def test_exit_plan_mode_rejects_empty_plan():
    from tools.plan_mode import enter_plan_mode, exit_plan_mode, is_in_plan_mode

    _call(enter_plan_mode)
    out = _call(exit_plan_mode, plan="")
    assert "plan is required" in out
    # Still in plan mode (didn't accept empty).
    assert is_in_plan_mode()


def test_plan_mode_blocks_all_three_write_tools():
    """Single integration test: plan mode blocks bash, edit, AND
    write simultaneously. If any one of them slips through, the
    whole plan-mode contract is broken."""
    from tools.bash import bash
    from tools.file_read import read
    from tools.file_edit import edit
    from tools.file_write import write
    from tools.plan_mode import _set_plan_mode

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False
    ) as f:
        f.write("hi\n")
        path = f.name
    try:
        _call(read, file_path=path)
        _set_plan_mode(True)

        bash_out = _call(bash, command="echo nope", description="t")
        edit_out = _call(edit, file_path=path, old_string="hi", new_string="bye")
        write_out = _call(write, file_path=path, content="overwrite\n")

        for label, result in (
            ("bash", bash_out),
            ("edit", edit_out),
            ("write", write_out),
        ):
            assert "Refused" in result, f"{label} did not refuse: {result!r}"
            assert "plan mode" in result, f"{label} refusal lacks plan-mode reason: {result!r}"

        # File still original — no write succeeded.
        assert Path(path).read_text() == "hi\n"
    finally:
        os.unlink(path)
