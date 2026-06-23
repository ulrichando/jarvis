"""Changed-line coverage gate for the auto-mod finalize step.

After a proposal's test suite passes, this checks that the lines the diff
**added** are actually executed by the suite. A self-modifying agent can
otherwise add code that no test touches and still be promoted to Review — the
suite is green only because nothing runs the new line. Verifying the change is
covered is the cheap, robust core of the Darwinian "Selection" pillar. (Full
mutation testing — mutmut — is a heavier later phase scoped to pure-stdlib
modules; see docs/superpowers/specs/2026-06-23-evolution-mutation-test-gate-design.md.)

Lives under pipeline/automod/ — on the auto-mod HARD BLOCKLIST — on purpose:
the agent can never edit the gate that judges it.

Reads the ``.coverage`` data file that finalize._rerun_pytest leaves behind
(it runs the suite under ``coverage run``). Pure in-process coverage API — no
subprocess, no path-matching guesswork. ``evaluate`` never raises; on any
problem it returns a ``skipped`` result so finalize is never broken by the gate.

Phases (env ``JARVIS_AUTOMOD_COVERAGE_GATE``):
  ``advisory`` (default) — record the score, never change proposal status.
  ``enforce``            — score < ``JARVIS_AUTOMOD_COVERAGE_MIN`` (default 0.7)
                           fails the proposal (handled in finalize).
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger("jarvis.automod.coverage_gate")

# new-file start line is group(1) of the hunk header "@@ -a,b +c,d @@"
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_SRC_PREFIX = "src/voice-agent/"


def added_lines_by_file(diff_text: str) -> dict[str, set[int]]:
    """Parse a unified diff → ``{repo-relative path: {added new-file line nums}}``.

    Restricted to ``src/voice-agent/**/*.py`` and excludes test files (a test
    doesn't need to be covered by another test)."""
    out: dict[str, set[int]] = {}
    cur: str | None = None
    new_ln = 0
    for line in diff_text.splitlines():
        if line.startswith("diff --git"):
            cur = None
            continue
        if line.startswith("+++ b/"):
            path = line[6:].strip()
            base = path.rsplit("/", 1)[-1]
            cur = path if (
                path.startswith(_SRC_PREFIX)
                and path.endswith(".py")
                and "/tests/" not in path
                and not base.startswith("test_")
            ) else None
            continue
        if line.startswith("@@"):
            m = _HUNK_RE.match(line)
            if m:
                new_ln = int(m.group(1))
            continue
        if cur is None or line.startswith("---"):
            continue
        if line.startswith("+"):
            out.setdefault(cur, set()).add(new_ln)
            new_ln += 1
        elif line.startswith("-"):
            continue  # removed line — does not advance the new-file counter
        else:
            new_ln += 1  # context line advances the new-file counter
    return out


def _result(status: str, *, score=None, covered=0, measurable=0,
            files=None, **extra) -> dict:
    out = {"status": status, "score": score, "covered": covered,
           "measurable": measurable, "files": files or {}}
    out.update(extra)
    return out


def evaluate(diff_text: str, *, cwd: Path, data_file: str = ".coverage") -> dict:
    """Score how much of the diff's ADDED code the suite executed.

    Returns a dict (never raises). ``score`` is None when there are no
    measurable (executable) added lines — e.g. a docs/comment-only change —
    which the caller treats as PASS. ``score`` is 0.0 when a brand-new module
    adds executable lines that no test imports."""
    changed = added_lines_by_file(diff_text)
    if not changed:
        return _result("no_python_changes")

    try:
        import coverage
        from coverage.python import PythonParser
    except Exception:  # noqa: BLE001
        return _result("skipped", reason="coverage not installed")

    cov_path = Path(cwd) / data_file
    if not cov_path.exists():
        return _result("skipped", reason="no coverage data")
    try:
        cov = coverage.Coverage(data_file=str(cov_path))
        cov.load()
        data = cov.get_data()
    except Exception as e:  # noqa: BLE001
        return _result("skipped", reason=f"coverage load failed: {e}")

    total_measurable = total_covered = 0
    per_file: dict[str, dict] = {}
    for repo_path, added in changed.items():
        abs_path = str(Path(cwd) / repo_path[len(_SRC_PREFIX):])
        try:
            parser = PythonParser(filename=abs_path)
            parser.parse_source()
            statements = set(parser.statements)
        except Exception:  # noqa: BLE001 — unparsable/missing file: skip, don't crash
            per_file[repo_path] = {"added": len(added), "measurable": 0,
                                   "covered": 0, "note": "not analyzable"}
            continue
        executed = set(data.lines(abs_path) or [])
        measurable = added & statements
        covered = added & executed
        entry = {"added": len(added), "measurable": len(measurable),
                 "covered": len(covered)}
        if measurable and not executed:
            entry["note"] = "no test imports this file"
        per_file[repo_path] = entry
        total_measurable += len(measurable)
        total_covered += len(covered)

    score = (total_covered / total_measurable) if total_measurable else None
    return _result(
        "scored" if score is not None else "no_executable_lines",
        score=round(score, 3) if score is not None else None,
        covered=total_covered, measurable=total_measurable, files=per_file,
    )
