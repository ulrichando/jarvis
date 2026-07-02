"""Diff validation for the auto-mod loop (Spec B, Plane 3).

validate_diff(diff_text) -> (ok: bool, reason: str). Applies these checks:
  1. non-empty diff + at least one diff header
  2. file count <= JARVIS_AUTOMOD_MAX_FILES (default 5)
  3. all changed files inside ALLOWED_PATH_PREFIX
  4. no changed file in HARD_BLOCKLIST_PATHS
  5. no test deletion (lines starting with '-def test_' or '-class Test')
  6. no new pytest.skip/skipif/xfail markers (lines starting with
     '+@pytest.mark.skip' / 'skipif' / 'xfail')
  7. total diff line count <= JARVIS_AUTOMOD_MAX_DIFF_LINES (default 2000)

Spec: docs/superpowers/specs/2026-05-24-jarvis-source-code-self-mod-design.md
"""
from __future__ import annotations

import os
import re

from pipeline.automod._state import is_blocked_path


# Matches both unquoted (`a/path`) and quoted (`"a/path with space"`) specs.
_FILE_HEADER_RE = re.compile(
    r'^diff --git (?P<a>"[^"]*"|\S+) (?P<b>"[^"]*"|\S+)', re.MULTILINE
)
_DELETED_TEST_RE = re.compile(r"^-\s*(?:async\s+)?def\s+test_\w+", re.MULTILINE)
_DELETED_TEST_CLASS_RE = re.compile(r"^-\s*class\s+Test\w+", re.MULTILINE)
_NEW_SKIP_RE = re.compile(
    r"^\+\s*@pytest\.mark\.(?:skip|skipif|xfail)", re.MULTILINE
)


def _max_files() -> int:
    try:
        return max(1, int(os.environ.get("JARVIS_AUTOMOD_MAX_FILES", "5")))
    except ValueError:
        return 5


def _max_lines() -> int:
    try:
        return max(100, int(os.environ.get("JARVIS_AUTOMOD_MAX_DIFF_LINES", "2000")))
    except ValueError:
        return 2000


def _strip_spec(spec: str) -> str:
    """Turn a header spec (`a/path`, `b/path`, or a quoted form) into a bare
    repo-relative path."""
    s = spec.strip().strip('"')
    if s.startswith("a/") or s.startswith("b/"):
        s = s[2:]
    return s


def _header_paths(diff_text: str) -> list[tuple[str, str]]:
    """(old, new) path pair per `diff --git` header."""
    return [
        (_strip_spec(m.group("a")), _strip_spec(m.group("b")))
        for m in _FILE_HEADER_RE.finditer(diff_text or "")
    ]


def files_changed(diff_text: str) -> list[str]:
    """Destination (b/) path per header, deduped, in order of first appearance.
    Destination is where content lands (and equals the source for a plain
    edit/new file)."""
    seen: list[str] = []
    seen_set: set[str] = set()
    for _old, new in _header_paths(diff_text):
        if new and new not in seen_set:
            seen_set.add(new)
            seen.append(new)
    return seen


def validate_diff(diff_text: str) -> tuple[bool, str]:
    """Validate a unified diff against the auto-mod safety gates.

    Returns (True, "") on success; (False, reason_str) on rejection.
    Reason strings are machine-readable: snake_case with embedded values
    after a colon where useful (e.g. "too_many_files:6>5").
    """
    if not diff_text or not diff_text.strip():
        return False, "empty_diff"

    # Fail closed: every `diff --git` header must parse into a path pair. A
    # header we cannot parse (exotic quoting) must REJECT, never be skipped.
    n_headers = len(re.findall(r"^diff --git ", diff_text, re.MULTILINE))
    if n_headers == 0:
        return False, "no_diff_headers"
    pairs = _header_paths(diff_text)
    if len(pairs) != n_headers:
        return False, "unparseable_diff_header"

    paths = files_changed(diff_text)        # destination paths (count + display)
    max_files = _max_files()
    if len(paths) > max_files:
        return False, f"too_many_files:{len(paths)}>{max_files}"

    # Blocklist BOTH sides of every header (a rename's source AND destination).
    for old, new in pairs:
        for candidate in (old, new):
            if candidate and is_blocked_path(candidate):
                return False, f"blocked_path:{candidate}"

    if _DELETED_TEST_RE.search(diff_text) or _DELETED_TEST_CLASS_RE.search(diff_text):
        return False, "test_deletion_detected"

    if _NEW_SKIP_RE.search(diff_text):
        return False, "new_pytest_skip_marker"

    total_lines = len(diff_text.splitlines())
    max_lines = _max_lines()
    if total_lines > max_lines:
        return False, f"diff_too_large:{total_lines}>{max_lines}_lines"

    return True, ""
