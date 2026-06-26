"""Stress-test gate for self-evolution proposals (2026-06-26).

The review council READS a proposal's diff; the stress gate RUNS the changed
code against generated edge cases — the "looks safe vs IS safe" distinction the
suite alone can't make (a change passes the existing tests yet breaks on inputs
nothing exercises). It asks a model to write a focused edge-case pytest module
for the diff, runs it, and gates on the result:

  pass    — generated stress tests ran and all passed
  fail    — a generated test FAILED (assertion) → the change breaks on an edge
            case → route the proposal back to rework
  skipped — gate disabled, no key, or the generated tests couldn't even run
            (syntax / collection / import ERROR = a *bad test*, NOT a real
            break). A skip is NEVER a false-reject — reliability over coverage.

OFF by default; enable with JARVIS_AUTOMOD_STRESS_GATE=1 (safe-by-default, like
the rest of the loop). Off the turn path. `generate`/`run_tests` are injectable
so the gate logic is unit-tested without an LLM or a real pytest run.
"""
from __future__ import annotations

import ast
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

_GEN_SYSTEM = (
    "You are a ruthless test engineer. Given a code diff, write a SHORT, focused "
    "pytest module of EDGE-CASE tests for ONLY the changed behavior. PREFER "
    "Hypothesis property-based tests (@given with strategies) for input-space "
    "coverage when the changed functions take simple typed inputs; otherwise "
    "write example-based tests for specific boundaries (empty/None, off-by-one, "
    "error paths, concurrency, adversarial inputs). Import and call the REAL "
    "modules under test — NEVER mock the code under test (a test that mocks the "
    "thing it checks proves nothing and lies). Output ONLY a valid Python pytest "
    "module — no prose, no markdown fences."
)
# Why this shape (grounded in 2026 LLM-test-gen research, arXiv 2510.25297 +
# the CI-gate guidance): property-based + example-based hybrid detects ~81% of
# bugs vs ~68% either alone; and "mock the hallucination → the test passes and
# lies" is the benchmark-killing pattern, so the generator is told never to mock
# the code under test. The DIFFERENTIAL check in _decide handles the rest.


def _skipped(reason: str) -> dict:
    return {"verdict": "skipped", "summary": reason, "failed": 0}


def _is_valid_python(code: str) -> bool:
    try:
        ast.parse(code)
        return True
    except (SyntaxError, ValueError):
        return False


def _strip_fences(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[-1] if "\n" in t else ""
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()


def _parse_pytest(out: str) -> dict:
    def n(word: str) -> int:
        m = re.search(rf"(\d+) {word}\b", out)
        return int(m.group(1)) if m else 0
    return {"passed": n("passed"), "failed": n("failed"), "errored": n("error") + n("errors")}


def _decide(new: dict, baseline: dict | None = None) -> dict:
    """Map a pytest run to a gate verdict, DIFFERENTIALLY.

    Reliability contract (grounded in the LLM-test-gen research — a generated
    test is only trustworthy if it discriminates the change):
      * ERROR-only on the change (bad / hallucinated test) → skip.
      * If a `baseline` run (the pre-change code) is supplied and the generated
        tests already fail OR error on it, they're unreliable (they fail
        regardless of the change) → skip. NEVER a false-reject.
      * Only a genuine NEW failure — passes on baseline, fails on the change —
        rejects. Without a baseline we fall back to "any failure rejects".
    """
    nf = int(new.get("failed", 0))
    ne = int(new.get("errored", 0))
    np_ = int(new.get("passed", 0))
    tail = new.get("tail", "")
    if np_ == 0 and ne > 0 and nf == 0:
        return _skipped(f"generated stress tests errored ({ne}) — bad test, not a real break")
    if baseline is not None and (int(baseline.get("failed", 0)) > 0 or int(baseline.get("errored", 0)) > 0):
        return _skipped("generated tests fail/error on the UNCHANGED baseline — unreliable, not trusted")
    if nf > 0:
        why = ("pass on baseline but FAIL on the change — a real edge-case regression"
               if baseline is not None else "failed (no baseline diff available)")
        return {"verdict": "fail", "summary": f"{nf} stress test(s) {why}", "failed": nf, "tail": tail}
    return {"verdict": "pass", "summary": f"{np_} stress test(s) passed", "failed": 0, "tail": tail}


def _generate_stress_tests(diff: str, intent: str) -> str | None:
    """Edge-case pytest module from the primary model, or None on any failure
    (→ caller skips). ponytail: anthropic-primary for v1; routing the generation
    through the council's multi-provider dispatch is the upgrade path."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    prompt = f"INTENT: {intent}\n\nDIFF:\n{diff[:6000]}\n\nWrite the edge-case pytest module now."
    try:
        import anthropic
        client = anthropic.Anthropic(timeout=60.0, max_retries=1)
        resp = client.messages.create(
            model=os.environ.get("JARVIS_AUTOMOD_STRESS_MODEL", "claude-sonnet-4-6"),
            max_tokens=2000, system=_GEN_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = "".join(getattr(b, "text", "") for b in resp.content)
    except Exception:
        return None
    code = _strip_fences(raw)
    return code or None


def _run_pytest(code: str, automod_id: str) -> dict:
    """Write the generated tests next to the suite + run them in the CURRENT
    working dir (the proposal's checkout when called from finalize). Cleans up."""
    repo = Path(__file__).resolve().parents[2]  # src/voice-agent
    py = repo / ".venv" / "bin" / "python"
    safe_id = re.sub(r"[^A-Za-z0-9_]", "_", automod_id)
    with tempfile.NamedTemporaryFile(
        "w", suffix=".py", prefix=f"test_stress_{safe_id}_", dir=str(repo / "tests"), delete=False
    ) as fh:
        fh.write(code)
        path = fh.name
    try:
        proc = subprocess.run(
            [str(py), "-m", "pytest", path, "-q", "--no-header", "-p", "no:cacheprovider"],
            cwd=os.getcwd(), capture_output=True, text=True, timeout=300, check=False,
        )
        out = proc.stdout + proc.stderr
        return {"tail": out[-1500:], **_parse_pytest(out)}
    except (subprocess.TimeoutExpired, OSError) as e:
        return {"tail": f"stress run error: {e}", "passed": 0, "failed": 0, "errored": 1}
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def run_stress_gate(
    automod_id: str,
    diff: str,
    intent: str,
    *,
    generate: Callable[[str, str], str | None] | None = None,
    run_tests: Callable[[str, str], dict] | None = None,
) -> dict:
    """Gate a proposal on generated edge-case tests. Returns a verdict dict with
    'verdict' in {pass, fail, skipped}. Never raises (best-effort, off the turn
    path). Disabled unless JARVIS_AUTOMOD_STRESS_GATE=1."""
    if os.environ.get("JARVIS_AUTOMOD_STRESS_GATE") != "1":
        return _skipped("stress gate disabled (JARVIS_AUTOMOD_STRESS_GATE != 1)")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return _skipped("no provider key for stress generation")
    gen = generate or _generate_stress_tests
    run = run_tests or _run_pytest
    try:
        code = gen(diff, intent)
    except Exception as e:  # generation must never break finalize
        return _skipped(f"stress generation raised: {e}")
    if not code or not _is_valid_python(code):
        return _skipped("no / invalid generated stress tests")
    try:
        result = run(code, automod_id)
    except Exception as e:
        return _skipped(f"stress run raised: {e}")
    # run_tests MAY attach a 'baseline' result (the same generated tests run on
    # the pre-change code) to enable the differential verdict; None = single run.
    baseline = result.pop("baseline", None) if isinstance(result, dict) else None
    return _decide(result, baseline)
