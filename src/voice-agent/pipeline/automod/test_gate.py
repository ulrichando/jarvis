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


_FILE_HEADER_RE = re.compile(r"^diff --git a/(\S+) b/(\S+)", re.MULTILINE)
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


def files_changed(diff_text: str) -> list[str]:
    """Extract 'a/<path>' files from each diff header. Returns deduped,
    in order of first appearance."""
    seen: list[str] = []
    seen_set: set[str] = set()
    for m in _FILE_HEADER_RE.finditer(diff_text or ""):
        path = m.group(1)
        if path not in seen_set:
            seen_set.add(path)
            seen.append(path)
    return seen


def validate_diff(diff_text: str) -> tuple[bool, str]:
    """Validate a unified diff against the auto-mod safety gates.

    Returns (True, "") on success; (False, reason_str) on rejection.
    Reason strings are machine-readable: snake_case with embedded values
    after a colon where useful (e.g. "too_many_files:6>5").
    """
    if not diff_text or not diff_text.strip():
        return False, "empty_diff"

    paths = files_changed(diff_text)
    if not paths:
        return False, "no_diff_headers"

    max_files = _max_files()
    if len(paths) > max_files:
        return False, f"too_many_files:{len(paths)}>{max_files}"

    for path in paths:
        if is_blocked_path(path):
            return False, f"blocked_path:{path}"

    if _DELETED_TEST_RE.search(diff_text) or _DELETED_TEST_CLASS_RE.search(diff_text):
        return False, "test_deletion_detected"

    if _NEW_SKIP_RE.search(diff_text):
        return False, "new_pytest_skip_marker"

    total_lines = len(diff_text.splitlines())
    max_lines = _max_lines()
    if total_lines > max_lines:
        return False, f"diff_too_large:{total_lines}>{max_lines}_lines"

    return True, ""
