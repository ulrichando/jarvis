"""Unit tests for `tools/bash.py` helpers — the internals the
@function_tool wraps.

Added 2026-05-12 after the bash.py audit that found:
  - subprocess called /bin/sh (dash), not /bin/bash → silent bashism failure
  - _DESTRUCTIVE_PATTERNS had mixed 2-tuple / 3-tuple shape
  - _BANNED_HINT had an orphan 'echo' key not in _BANNED_COMMANDS
  - _check_banned only inspected the first shlex-token (missed
    `ls; cat foo` etc.)
  - _truncate cut mid-line

Each test corresponds to one of those fixes plus a few coverage
holes (case-insensitive SQL, the `$(...)` subshell separator).
"""
from __future__ import annotations

import re

import pytest

from tools.bash import (
    _BANNED_COMMANDS,
    _BANNED_HINT,
    _CLAUSE_RE,
    _DESTRUCTIVE_PATTERNS,
    _check_banned,
    _check_destructive,
    _truncate,
)


# ── _DESTRUCTIVE_PATTERNS shape ────────────────────────────────────


def test_destructive_patterns_uniform_shape():
    """Every entry must be a (compiled_regex, label) 2-tuple — the
    pre-fix code mixed 2-tuples and 3-tuples for SQL flags, and the
    detection function had a branch to handle both. Pre-compiling
    folded flags into the Pattern, so all entries are now uniform."""
    for entry in _DESTRUCTIVE_PATTERNS:
        assert len(entry) == 2, f"non-uniform entry: {entry!r}"
        pattern, label = entry
        assert isinstance(pattern, re.Pattern), (
            f"{label}: expected compiled re.Pattern, got {type(pattern).__name__}"
        )
        assert isinstance(label, str) and label, f"empty label: {entry!r}"


def test_destructive_case_insensitive_sql():
    """SQL patterns carry re.I so the supervisor can't sneak past
    `drop table users` in lowercase. Pre-fix, that flag was a 3-tuple
    element; the 2-tuple branch missed it half the time depending on
    insertion order. The compiled-regex approach makes it permanent."""
    assert _check_destructive("DROP TABLE users") == "SQL DROP TABLE"
    assert _check_destructive("drop table users") == "SQL DROP TABLE"
    assert _check_destructive("Drop Table users") == "SQL DROP TABLE"
    assert _check_destructive("truncate table audit") == "SQL TRUNCATE TABLE"


@pytest.mark.parametrize("cmd,label", [
    ("rm -rf /",                                 "rm -rf with absolute path"),
    ("rm -rf ~",                                 "rm -rf with home expansion"),
    ("sudo rm /etc/passwd",                      "sudo rm"),
    ("dd if=/dev/zero of=/dev/sda",              "dd to disk"),
    ("mkfs.ext4 /dev/sdb1",                      "mkfs (formatting filesystem)"),
    ("git push --force origin main",             "git force push"),
    ("git push -f origin main",                  "git force push"),
    ("git reset --hard HEAD~5",                  "git reset --hard"),
    ("git clean -fd",                            "git clean -fd"),
    ("cat /dev/urandom > /dev/sda",              "writing to a raw disk device"),
])
def test_destructive_known_dangers_caught(cmd, label):
    assert _check_destructive(cmd) == label


@pytest.mark.parametrize("cmd", [
    "ls -la",
    "git status",
    "git log --oneline",
    "echo hello",
    "rm tempfile.txt",                # rm without -rf or /
    "sudo systemctl restart foo",     # sudo without rm
])
def test_destructive_safe_commands_not_flagged(cmd):
    assert _check_destructive(cmd) is None


# ── _BANNED_HINT / _BANNED_COMMANDS coherence ────────────────────


def test_banned_hint_has_no_orphans():
    """Every key in _BANNED_HINT must be in _BANNED_COMMANDS — pre-
    fix, 'echo' was in the hint dict but not the banned set, so the
    hint was dead code. Tests this invariant so future additions
    don't reintroduce the orphan."""
    orphans = set(_BANNED_HINT) - _BANNED_COMMANDS
    assert not orphans, f"hint keys with no corresponding banned command: {orphans}"


def test_echo_is_not_banned():
    """Echo is legitimately useful in pipelines (echo | xargs etc.)
    and as a building block — banning it would block too many real
    uses. The hint was over-zealous claude-code-style coaching."""
    assert _check_banned("echo hello") is None
    assert _check_banned('echo "data" | xargs ls') is None


# ── _check_banned multi-clause detection ──────────────────────────


@pytest.mark.parametrize("cmd,expected_name", [
    # Banned at start of command
    ("cat /etc/hostname",                "cat"),
    ("head -n 5 file.txt",               "head"),
    ("tail -f /var/log/syslog",          "tail"),
    ("less /etc/passwd",                 "less"),
    ("more file.txt",                    "more"),
    ("sed -i s/foo/bar/g file.txt",      "sed"),
    ("awk '{print $1}' file.txt",        "awk"),

    # Banned after clause boundaries
    ("ls; cat foo",                      "cat"),
    ("ls && cat foo",                    "cat"),
    ("ls || cat foo",                    "cat"),
    ("ls | cat foo",                     "cat"),
    ("(cat foo)",                        "cat"),
    ("echo before; cat foo",             "cat"),

    # Command substitution — $(...) and (...) are subshells
    ("X=$(cat foo)",                     "cat"),
    ("Y=$(ls -la; cat /etc/hostname)",   "cat"),
])
def test_banned_caught_at_clause_starts(cmd, expected_name):
    result = _check_banned(cmd)
    assert result is not None, f"expected {expected_name} to be flagged in: {cmd}"
    name, hint = result
    assert name == expected_name, f"got {name}, expected {expected_name}"
    assert hint, "hint must not be empty"


@pytest.mark.parametrize("cmd", [
    # Banned NAMES appearing as arguments — must NOT be flagged
    "grep cat /etc/passwd",              # cat is a search pattern
    "find . -name cat",                  # cat is a filename pattern
    "find . -name cat -delete",
    "ls --color=auto cat",               # cat is an arg to ls
    "wc -l cat.txt",                     # cat.txt is a filename
    "ls /var/cat",                       # /var/cat is a directory path
    "xargs cat < ids.txt",               # xargs is the command; cat is its argument

    # Backslash escape — explicit opt-out of the coaching
    "\\cat foo",
    "ls; \\cat foo",

    # Non-banned commands
    "ls -la",
    "git status",
    "echo hello world",
    "",
    "   ",                               # whitespace-only
])
def test_banned_not_flagged_for_arguments_or_escapes(cmd):
    assert _check_banned(cmd) is None, f"false positive: {cmd!r}"


def test_check_banned_returns_tuple_shape():
    """Public contract: returns (banned_name, hint) tuple or None.
    The wrapping bash() function uses the name in its suggestion
    string ('instead of `cat`...') so the type matters."""
    result = _check_banned("cat foo")
    assert isinstance(result, tuple)
    assert len(result) == 2
    name, hint = result
    assert isinstance(name, str)
    assert isinstance(hint, str)


def test_clause_re_matches_separators():
    """Regression: explicit assertion of what _CLAUSE_RE considers
    a clause boundary, so future edits to the regex can't silently
    drop one without test failure."""
    # First token always matches
    assert _CLAUSE_RE.search("foo bar")
    # Each separator should produce a match for the trailing word
    for sep_phrase in ["a; foo", "a && foo", "a || foo", "a | foo",
                       "a$(foo)", "a`foo`", "a(foo)"]:
        matches = list(_CLAUSE_RE.finditer(sep_phrase))
        names = [m.group(1) for m in matches]
        assert "foo" in names, f"separator failed in {sep_phrase!r}: got {names}"


# ── _truncate newline snapping ────────────────────────────────────


def test_truncate_returns_input_when_under_limit():
    text = "short output"
    assert _truncate(text, limit=100) == text


def test_truncate_emits_one_marker_when_over():
    text = "x" * 100_000
    out = _truncate(text, limit=10_000)
    assert "[output truncated:" in out
    # Exactly one elision marker — no double-truncation.
    assert out.count("[output truncated:") == 1


def test_truncate_snaps_to_newline_boundary():
    """Pre-fix, _truncate cut at exact char offsets and could leave
    half-lines on both sides ('line-0123: xxxx[...]xx12: ...'). Now
    it snaps to the nearest newline so both sides start/end on a
    clean line break."""
    lines = "\n".join(f"line-{i:04d}: " + "x" * 60 for i in range(2000))
    truncated = _truncate(lines, limit=10_000)
    head_part, rest = truncated.split("[output truncated:", 1)
    tail_part = rest.split("omitted from the middle]\n\n", 1)[1]

    # Head must end on a complete line (terminating x's from a full line)
    assert head_part.rstrip().endswith("x" * 60), (
        f"head should end on full line, got tail: {head_part[-80:]!r}"
    )
    # Tail must START on a complete line.
    assert tail_part.startswith("line-"), (
        f"tail should start on a line boundary, got: {tail_part[:80]!r}"
    )


def test_truncate_handles_input_with_no_newlines():
    """Edge case: a giant single-line blob (e.g., minified JSON).
    No newlines exist within the snap window, so the function falls
    back to the raw character offsets without raising."""
    text = "x" * 100_000
    out = _truncate(text, limit=10_000)
    assert "[output truncated:" in out
    # Length sanity: should be roughly limit + the marker text.
    assert 9_000 < len(out) < 12_000


def test_truncate_handles_tiny_input_above_limit():
    """Edge case: limit smaller than the snap window. The defensive
    bail-out path (head_end >= tail_start after snapping) should
    fall back to the raw midpoint cut without breaking."""
    text = "abcdefghijklmnopqrstuvwxyz" * 10  # 260 chars
    out = _truncate(text, limit=50)
    assert "[output truncated:" in out


# ── cwd persistence (2026-05-12) ────────────────────────────────


import asyncio
import pytest

from tools.bash import (
    _CWD_SENTINEL,
    _extract_new_cwd,
    _wrap_for_cwd_capture,
    bash,
    reset_cwd_for_test,
)


def _unwrap(tool):
    for attr in ("__livekit_agents_func", "_func", "fnc", "func", "callable"):
        f = getattr(tool, attr, None)
        if callable(f):
            return f
    if callable(tool):
        return tool
    raise RuntimeError(f"can't unwrap {tool!r}")


def _call(tool, **kwargs):
    return asyncio.run(_unwrap(tool)(**kwargs))


@pytest.fixture(autouse=True)
def _isolate_bash_cwd():
    """Every test starts with a fresh cwd cache so test-order doesn't
    matter and a `cd /tmp` in one test can't bleed into another."""
    reset_cwd_for_test()
    yield
    reset_cwd_for_test()


def test_wrap_for_cwd_capture_uses_newlines_not_semicolons():
    """A trailing `#` comment inside cmd must NOT consume the
    wrapper's pwd-capture line. Newline separation avoids that."""
    wrapped = _wrap_for_cwd_capture("echo hi  # trailing comment")
    # The sentinel printf and exit must be on lines AFTER cmd, not
    # `;`-chained.
    assert "\n" in wrapped
    sentinel_line_idx = wrapped.find(_CWD_SENTINEL)
    newline_before = wrapped.rfind("\n", 0, sentinel_line_idx)
    assert newline_before >= 0, "sentinel must be preceded by a newline"


def test_extract_new_cwd_finds_sentinel_line():
    text = f"some output\n\n{_CWD_SENTINEL}/tmp/foo\n"
    stripped, new_cwd = _extract_new_cwd(text)
    assert new_cwd == "/tmp/foo"
    # Sentinel line must NOT appear in the visible output.
    assert _CWD_SENTINEL not in stripped


def test_extract_new_cwd_missing_sentinel_returns_none():
    text = "just plain output\nnothing to see\n"
    stripped, new_cwd = _extract_new_cwd(text)
    assert new_cwd is None
    assert stripped == text


def test_cwd_persists_across_two_bash_calls():
    _call(bash, command="cd /tmp")
    out = _call(bash, command="pwd")
    # `/tmp` followed by exit marker — output may include extras
    # depending on shell init, but pwd's first line must be /tmp.
    first_line = out.splitlines()[0]
    assert first_line == "/tmp", f"expected /tmp, got {first_line!r}"


def test_cwd_persists_after_chained_cd():
    """`cd /tmp && ls` should leave us in /tmp for the next call."""
    _call(bash, command="cd /tmp && ls > /dev/null")
    out = _call(bash, command="pwd")
    assert out.splitlines()[0] == "/tmp"


def test_cwd_unchanged_when_cmd_doesnt_cd():
    """A pwd-only command obviously doesn't change cwd, but the
    wrapper still captures the pwd → cache stays consistent."""
    first = _call(bash, command="pwd").splitlines()[0]
    second = _call(bash, command="pwd").splitlines()[0]
    assert first == second


def test_sentinel_never_appears_in_returned_output():
    """User must NEVER see the sentinel string in the supervisor's
    chat_ctx — it's an internal protocol."""
    out = _call(bash, command="echo hello")
    assert _CWD_SENTINEL not in out


def test_disable_persistence_via_env(monkeypatch):
    """JARVIS_BASH_PERSIST_CWD=0 opt-out: no sentinel wrapping,
    no cwd cache update. cd inside a call doesn't survive."""
    _call(bash, command="cd /tmp && pwd")  # warms the cache to /tmp
    monkeypatch.setenv("JARVIS_BASH_PERSIST_CWD", "0")
    # First call after opt-out — cache still /tmp from before, but
    # the next call doesn't update it. cd to / and then pwd should
    # show / because the same call is one shell.
    out = _call(bash, command="cd / && pwd")
    assert out.splitlines()[0] == "/"
    # Persistence is off, so the cache shouldn't update. The next
    # call should start from the process cwd (NOT /), since
    # bash_cwd = None when persist is off.
    out2 = _call(bash, command="pwd")
    # Just confirm no sentinel leaked and the call succeeded.
    assert _CWD_SENTINEL not in out2
    assert "[exit 0]" in out2


def test_exit_code_preserved_through_wrapper():
    """The wrapper captures $? before printing the sentinel and
    re-exits with it — `exit 42` must surface as [exit 42] to the
    caller, not [exit 0]."""
    out = _call(bash, command="exit 42")
    assert "[exit 42]" in out


def test_trailing_comment_in_cmd_doesnt_break_wrapper():
    """Regression: with `;`-chained wrapper stanzas, a `#` comment
    in cmd ate the sentinel printf. The newline-separated wrapper
    handles this correctly."""
    out = _call(bash, command="echo ok  # trailing comment")
    assert "ok" in out
    assert "[exit 0]" in out
    assert _CWD_SENTINEL not in out
