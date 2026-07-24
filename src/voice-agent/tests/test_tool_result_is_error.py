"""Tests for tool_result_is_error — high-precision tool-failure classifier.

Scope (2026-07-24): this helper feeds the had_tool_error TELEMETRY flag
only. It does NOT gate replies. Two attempts to enforce mode-2 ("claims
done without checking") in the pre-TTS filter were reverted after
preflight — attempt 1 (error-aware has_tool_results) could route a
failed tool + success-claim into a retry chain that goes silent;
attempt 2 (a deterministic honest-error hedge) fired on the shared
session.say() rail and hijacked unrelated announcements. So these are
pure classifier unit tests.

The classifier is deliberately HIGH PRECISION (explicit signals only —
truthy "error" / status=="error" / "ok": false / "Error:" or "Browser
task failed" string prefix). It does NOT infer failure from a non-zero
exit_code: benign non-zero exits (grep no-match, pkill/systemctl
is-active/which not-found) are indistinguishable from real failures at
that level, and real terminal failures set a truthy "error" anyway.

Real shapes below are copied from the live tools:
  * terminal_tool.py — success is {"output", "exit_code", "error": null}
    (+ an "exit_code_meaning" note on benign non-zero exits); real
    failures (timeout / exec-fail / spawn-fail / blocked) set "error".
  * computer_use.py — a failed ActionResult is {"ok": false, "action",
    "message"} with NO error/status/exit_code key (_result_succeeded).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from confab_detector import tool_result_is_error


# --- real ERROR shapes (must return True) ---------------------------------

def test_tool_error_json():
    # registry.tool_error("...") → {"error": "..."}
    assert tool_result_is_error('{"error": "file not found"}')


def test_terminal_exec_failure():
    # terminal_tool exec-fail path: exit_code from returncode (default -1),
    # truthy "error" — NOT exit 1 (a command that RAN and exited 1 gets
    # error: null; see the success section).
    assert tool_result_is_error(
        '{"output": "", "exit_code": -1, "error": "Command execution failed: boom"}'
    )


def test_terminal_timeout():
    assert tool_result_is_error(
        '{"output": "", "exit_code": 124, "error": "Command timed out after 30 seconds"}'
    )


def test_status_error():
    assert tool_result_is_error(
        '{"output": "", "exit_code": -1, "error": "x", "status": "error"}'
    )


def test_status_error_branch_isolated():
    # No "error" key at all — the status branch alone must decide.
    assert tool_result_is_error('{"output": "", "status": "error"}')


def test_computer_use_failed_action():
    # computer_use _text_response for a failed ActionResult — no
    # error/status/exit_code key, just "ok": false.
    assert tool_result_is_error(
        '{"ok": false, "action": "click", "message": "element index 42 out of range"}'
    )


def test_adapter_catchall_string():
    # tools/_adapter._run swallows exceptions → plain "Error: <tool> failed: ..."
    assert tool_result_is_error("Error: terminal failed: KeyError('x')")


def test_browser_failed_string():
    assert tool_result_is_error("Browser task failed: navigation timeout")


def test_dict_payload_not_just_string():
    assert tool_result_is_error({"error": "nope", "exit_code": 2})


# --- real SUCCESS shapes (must return False) ------------------------------

def test_terminal_success():
    # error is null, exit_code 0 — the common success shape
    assert not tool_result_is_error('{"output": "hello", "exit_code": 0, "error": null}')


def test_grep_no_match_is_success():
    # THE false-positive class: "check if X is in the file" → grep exit 1
    # = clean answer. terminal_tool itself annotates it non-erroneous.
    assert not tool_result_is_error(
        '{"output": "", "exit_code": 1, "error": null,'
        ' "exit_code_meaning": "No matches found (not an error)"}'
    )


def test_diff_files_differ_is_success():
    assert not tool_result_is_error(
        '{"output": "1c1\\n< a\\n---\\n> b", "exit_code": 1, "error": null,'
        ' "exit_code_meaning": "Files differ (expected, not an error)"}'
    )


def test_git_diff_nonzero_is_success():
    assert not tool_result_is_error(
        '{"output": "diff --git ...", "exit_code": 1, "error": null,'
        ' "exit_code_meaning":'
        ' "Non-zero exit (often normal \\u2014 e.g. \'git diff\' returns 1 when files differ)"}'
    )


def test_find_partial_results_is_success():
    assert not tool_result_is_error(
        '{"output": "/home/x/a.txt", "exit_code": 1, "error": null,'
        ' "exit_code_meaning":'
        ' "Some directories were inaccessible (partial results may still be valid)"}'
    )


def test_computer_use_ok_action():
    assert not tool_result_is_error('{"ok": true, "action": "click"}')


def test_plain_success_string():
    assert not tool_result_is_error("Clicked the Submit button.")


def test_read_file_contents_mentioning_error():
    # A file whose CONTENTS talk about errors must NOT trip — prefix-anchored.
    assert not tool_result_is_error("def handle():\n    # Error: handling below\n    pass")


def test_json_result_no_error_key():
    assert not tool_result_is_error('{"success": true, "count": 42}')


def test_none_and_empty():
    assert not tool_result_is_error(None)
    assert not tool_result_is_error("")
    assert not tool_result_is_error("   ")


def test_exit_code_zero_ignored():
    assert not tool_result_is_error({"exit_code": 0})


def test_error_null_ignored():
    assert not tool_result_is_error({"error": None})


# --- deliberately NOT flagged: non-zero exit with error:null -------------
# The classifier does NOT infer failure from exit_code, because benign and
# real non-zero exits are indistinguishable at that level and only SOME are
# annotated. This gates a reply-facing hedge, so a false positive here would
# make JARVIS wrongly say "that errored out" over a success — worse than
# missing an exit-code-only failure (whose real failures set "error" anyway).

def test_pkill_no_match_not_flagged():
    # `pkill spotify` when Spotify isn't running → exit 1, error null, NO
    # annotation. Must NOT flag, or "kill Spotify" → false "that errored out".
    assert not tool_result_is_error('{"output": "", "exit_code": 1, "error": null}')


def test_systemctl_inactive_not_flagged():
    # `systemctl is-active foo` → exit 3 = inactive (a valid answer), error null.
    assert not tool_result_is_error('{"output": "inactive", "exit_code": 3, "error": null}')


def test_grep_no_match_no_annotation_not_flagged():
    # Even without the exit_code_meaning annotation, a bare non-zero exit
    # with error:null is not treated as a failure.
    assert not tool_result_is_error('{"output": "", "exit_code": 1, "error": null}')


def test_curl_exit_only_not_flagged():
    # curl "could not resolve host" surfaces as exit 6 + error:null; the
    # exit-code-only signal is deliberately NOT used (real terminal-level
    # failures set a truthy "error"). Accepted miss for zero false positives.
    assert not tool_result_is_error(
        '{"output": "", "exit_code": 6, "error": null,'
        ' "exit_code_meaning": "Could not resolve host"}'
    )


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
