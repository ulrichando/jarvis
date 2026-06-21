"""Plain-English summary of a self-evolution proposal.

Turns an auto-mod artifact (intent + files + diff stat + pytest tail) into:
  - a PR title + markdown body (review the change from your phone), and
  - a short one-liner for the in-app /evolution card.

Pure + side-effect-free so it's trivially testable and safe to call anywhere.
"""
from __future__ import annotations

from typing import Any, Dict, List


def _tests_ok(test_tail: str) -> bool:
    """Heuristic over a pytest tail: green only if it mentions 'passed' and no
    'failed'/'error'. Conservative — an ambiguous tail reads as not-ok so the
    summary flags it for a human look rather than implying a clean run."""
    low = (test_tail or "").lower()
    if not low:
        return False
    return "passed" in low and "failed" not in low and "error" not in low


def summarize(art: Dict[str, Any]) -> Dict[str, Any]:
    """Summarize one artifact dict. Returns {title, markdown, short, tests_ok}."""
    automod_id = str(art.get("id", "")).strip()
    intent = (art.get("intent") or "").strip()
    files: List[str] = list(art.get("files_changed") or [])
    diff_summary = (art.get("diff_summary") or "").strip()
    test_tail = (art.get("test_output_tail") or "").strip()
    tests_ok = _tests_ok(test_tail)

    title = (intent.splitlines()[0][:72] if intent
             else f"JARVIS self-evolution {automod_id}")

    file_lines = "\n".join(f"- `{f}`" for f in files) or "_(none listed)_"
    tests_badge = "✅ pytest passed" if tests_ok else "⚠️ check the test output"
    test_block = (
        f"```\n{test_tail[-1500:]}\n```" if test_tail else "_(no test output captured)_"
    )

    markdown = (
        f"## What this changes\n{intent or '_(no intent recorded)_'}\n\n"
        f"## Files changed ({len(files)})\n{file_lines}\n\n"
        f"## Diff summary\n{diff_summary or '_(n/a)_'}\n\n"
        f"## Tests\n{tests_badge}\n\n{test_block}\n\n"
        "---\n"
        f"_Proposed by JARVIS self-evolution (`{automod_id}`). Review the diff, "
        "then **approve to deploy** — the deploy watchdog auto-rolls-back if the "
        "new code is unhealthy._"
    )

    short = (
        f"{title} — {len(files)} file"
        f"{'s' if len(files) != 1 else ''}, "
        f"{'tests pass' if tests_ok else 'check tests'}"
    )
    return {"title": title, "markdown": markdown, "short": short, "tests_ok": tests_ok}


def summarize_id(automod_id: str) -> Dict[str, Any]:
    """Load an artifact by id and summarize it."""
    from pipeline.automod import artifact
    return summarize(artifact.load(automod_id))
