"""Tests for the evolution changed-line coverage gate.

Hermetic: builds a coverage data file via the public CoverageData API (no
nested `coverage run`, no full-suite dependency), so it's safe even when the
whole suite is itself run under `coverage run` by finalize._rerun_pytest.
"""
from __future__ import annotations

import coverage

from pipeline.automod import coverage_gate


def test_added_lines_by_file_parses_and_filters():
    diff = (
        "diff --git a/src/voice-agent/foo.py b/src/voice-agent/foo.py\n"
        "--- a/src/voice-agent/foo.py\n"
        "+++ b/src/voice-agent/foo.py\n"
        "@@ -1,2 +1,4 @@\n"
        " import os\n"
        "+x = 1\n"
        "+y = 2\n"
        " z = 3\n"
        # a test file — must be excluded
        "diff --git a/src/voice-agent/tests/test_foo.py b/src/voice-agent/tests/test_foo.py\n"
        "+++ b/src/voice-agent/tests/test_foo.py\n"
        "@@ -1 +1,2 @@\n"
        "+def test_new():\n"
        # a non-voice-agent, non-.py file — must be excluded
        "diff --git a/README.md b/README.md\n"
        "+++ b/README.md\n"
        "@@ -1 +1,2 @@\n"
        "+docs\n"
    )
    assert coverage_gate.added_lines_by_file(diff) == {"src/voice-agent/foo.py": {2, 3}}


def test_evaluate_no_python_changes_passes():
    # docs-only / non-python diff → nothing to measure → PASS (score None).
    diff = "diff --git a/README.md b/README.md\n+++ b/README.md\n@@ -1 +1,2 @@\n+docs\n"
    res = coverage_gate.evaluate(diff, cwd=".")
    assert res["status"] == "no_python_changes"
    assert res["score"] is None


def test_evaluate_skips_without_coverage_data(tmp_path):
    diff = "+++ b/src/voice-agent/foo.py\n@@ -1 +1,2 @@\n+x = 1\n"
    res = coverage_gate.evaluate(diff, cwd=tmp_path)
    assert res["status"] == "skipped"
    assert res["score"] is None


def test_evaluate_scores_covered_vs_orphan(tmp_path):
    # covered.py is "executed"; orphan.py never is. A diff that adds a line in
    # each should score 1/2 = 0.5 (the orphan's added line is uncovered).
    (tmp_path / "covered.py").write_text("def f():\n    return 1\n")
    (tmp_path / "orphan.py").write_text("def g():\n    return 2\n")
    data = coverage.CoverageData(basename=str(tmp_path / ".coverage"))
    data.add_lines({str(tmp_path / "covered.py"): [1, 2]})
    data.write()

    diff = (
        "+++ b/src/voice-agent/covered.py\n@@ -1,1 +1,2 @@\n def f():\n+    return 1\n"
        "+++ b/src/voice-agent/orphan.py\n@@ -1,1 +1,2 @@\n def g():\n+    return 2\n"
    )
    res = coverage_gate.evaluate(diff, cwd=tmp_path)
    assert res["status"] == "scored"
    assert res["covered"] == 1
    assert res["measurable"] == 2
    assert res["score"] == 0.5
    assert res["files"]["src/voice-agent/orphan.py"].get("note") == "no test imports this file"
