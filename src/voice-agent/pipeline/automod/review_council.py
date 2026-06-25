"""3-lens review council for pending self-evolution proposals (2026-06-25).

When a build produces a `pending` proposal, an automatic council reviews its
diff through three INDEPENDENT lenses — correctness, security, regression —
each a single structured Anthropic pass (mirrors introspection.py). The lens
verdicts are fused worst-of into one recommendation and written to
~/.jarvis/auto-mods/<id>.review.json, which GET /api/evolution surfaces so the
reviewer sees the council's read BEFORE deciding to deploy.

ADVISORY ONLY. It never gates or blocks a deploy automatically — a human still
approves every deploy; this only informs that decision. Best-effort: no key /
model error / unparseable output each degrade to a 'skipped' lens (NOT a pass)
and never break finalize. Off the turn path — runs in the build subprocess
after a pending artifact is written, or on demand via bin/jarvis-evolution-review.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from pipeline.automod._state import _automod_home
from pipeline.automod.introspection import _parse_json_object

logger = logging.getLogger("jarvis.automod.review")

DEFAULT_REVIEW_MODEL = "claude-sonnet-4-6"
_DIFF_CAP = 24000  # bound the prompt; proposals are capped at <=5 files / 2000 diff lines

# verdict severity ordering — fusion takes the worst across lenses.
_SEVERITY = {"pass": 0, "concern": 1, "block": 2}

LENSES: dict[str, str] = {
    "correctness": (
        "You are the CORRECTNESS reviewer. Decide whether this diff correctly "
        "implements its stated intent. Hunt for logic bugs, inverted or wrong "
        "conditions, off-by-one errors, missed edge cases, broken control flow, "
        "wrong types, and code that does not actually do what the intent claims. "
        "Ignore style and security — other reviewers cover those."
    ),
    "security": (
        "You are the SECURITY reviewer. Hunt for security issues introduced by "
        "this diff: command/shell injection, path traversal, unsafe "
        "deserialization or eval/exec, hard-coded secrets, unsafe file writes, "
        "auth or permission bypass, SSRF, or weakened input validation. A diff "
        "with no security-relevant change is a 'pass'."
    ),
    "regression": (
        "You are the REGRESSION reviewer. Decide whether this diff risks breaking "
        "EXISTING behavior. Flag: removed or weakened tests/guards/assertions, "
        "changed function signatures or return contracts that other call sites "
        "depend on, altered default values, and behavior changes with no covering "
        "test. Purely additive code with its own tests is low risk."
    ),
}

_RUBRIC = (
    "Respond with ONLY a JSON object, no prose:\n"
    '{"verdict": "pass" | "concern" | "block", '
    '"findings": ["<specific; cite file:line where you can>", ...], '
    '"summary": "<one sentence>"}\n'
    "verdict guide: pass = nothing wrong in YOUR lens; concern = worth a human "
    "look but not necessarily blocking; block = a real defect that should stop "
    "the deploy. Keep findings concrete and few (max 5)."
)


def _model() -> str:
    return os.environ.get("JARVIS_REVIEW_MODEL", DEFAULT_REVIEW_MODEL)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _review_path(automod_id: str) -> Path:
    return _automod_home() / f"{automod_id}.review.json"


def _skipped(reason: str) -> dict:
    return {"verdict": "skipped", "findings": [], "summary": reason}


def _review_one(lens: str, instruction: str, intent: str, diff: str) -> dict:
    """One structured LLM pass for a single lens. Best-effort → 'skipped' on any
    failure; an unknown/missing verdict normalizes to 'concern' (never a silent
    pass)."""
    try:
        import anthropic

        client = anthropic.Anthropic(timeout=45.0, max_retries=1)
        prompt = (
            f"{instruction}\n\n"
            f"PROPOSAL INTENT:\n{(intent or '')[:1500]}\n\n"
            f"UNIFIED DIFF (truncated to {_DIFF_CAP} chars):\n{(diff or '')[:_DIFF_CAP]}\n\n"
            f"{_RUBRIC}"
        )
        resp = client.messages.create(
            model=_model(),
            max_tokens=1200,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(getattr(b, "text", "") for b in resp.content).strip()
    except Exception as e:  # noqa: BLE001 — advisory; degrade, never raise
        logger.warning("[review] %s lens call failed: %s", lens, e)
        return _skipped(f"model call failed: {e}")

    parsed = _parse_json_object(text)
    if not parsed:
        return _skipped("unparseable model output")
    verdict = str(parsed.get("verdict", "")).lower().strip()
    if verdict not in _SEVERITY:
        verdict = "concern"  # unknown → flag for a human; do not silently pass
    findings = [str(f) for f in (parsed.get("findings") or [])][:5]
    return {"verdict": verdict, "findings": findings, "summary": str(parsed.get("summary", "")).strip()}


def _fuse(lenses: dict[str, dict]) -> dict:
    """Worst-of fusion → overall verdict + recommendation. 'skipped' lenses do
    NOT count toward severity but ARE recorded, so a skipped lens never reads as
    a pass. If every lens skipped, the overall is 'skipped' (recommend review)."""
    scored = [(l, _SEVERITY[v["verdict"]]) for l, v in lenses.items() if v["verdict"] in _SEVERITY]
    skipped = sorted(l for l, v in lenses.items() if v["verdict"] == "skipped")
    if not scored:
        return {"verdict": "skipped", "recommendation": "review",
                "blocking_lenses": [], "concern_lenses": [], "skipped": skipped}
    worst = max(s for _, s in scored)
    verdict = {0: "pass", 1: "concern", 2: "block"}[worst]
    recommendation = {"pass": "approve", "concern": "caution", "block": "reject"}[verdict]
    return {
        "verdict": verdict,
        "recommendation": recommendation,
        "blocking_lenses": sorted(l for l, s in scored if s == 2),
        "concern_lenses": sorted(l for l, s in scored if s == 1),
        "skipped": skipped,
    }


def _write(automod_id: str, review: dict) -> None:
    try:
        p = _review_path(automod_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(review, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError as e:
        logger.warning("[review] write failed for %s: %s", automod_id, e)


def _all_skipped_review(automod_id: str, reason: str) -> dict:
    review = {
        "automod_id": automod_id,
        "model": _model(),
        "generated_at": _now_iso(),
        "overall": {"verdict": "skipped", "recommendation": "review",
                    "blocking_lenses": [], "concern_lenses": [], "skipped": sorted(LENSES)},
        "lenses": {l: _skipped(reason) for l in LENSES},
    }
    _write(automod_id, review)
    return review


def review_proposal(automod_id: str, diff: str, intent: str) -> dict:
    """Run the 3-lens council on one proposal; persist + return the review.

    ADVISORY: writes <id>.review.json but never changes the proposal's status —
    a human still approves. Best-effort; never raises."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return _all_skipped_review(automod_id, "no ANTHROPIC_API_KEY")
    if not (diff or "").strip():
        return _all_skipped_review(automod_id, "no diff to review")

    lenses = {l: _review_one(l, instr, intent, diff) for l, instr in LENSES.items()}
    review = {
        "automod_id": automod_id,
        "model": _model(),
        "generated_at": _now_iso(),
        "overall": _fuse(lenses),
        "lenses": lenses,
    }
    _write(automod_id, review)
    try:
        from pipeline.automod import artifact

        artifact.audit("automod_reviewed", id=automod_id,
                       verdict=review["overall"]["verdict"],
                       recommendation=review["overall"]["recommendation"])
    except Exception:  # noqa: BLE001 — audit must never break the review
        pass
    return review


def read_review(automod_id: str) -> dict | None:
    """Return the last stored review for an id, or None."""
    try:
        return json.loads(_review_path(automod_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _main(argv: list[str]) -> int:
    """CLI: `python -m pipeline.automod.review_council <automod_id>` — reviews a
    proposal by reading the diff stored in its artifact. Used by
    bin/jarvis-evolution-review (on-demand from the web)."""
    if not argv:
        print(json.dumps({"error": "usage: review_council <automod_id>"}))
        return 2
    automod_id = argv[0]
    from pipeline.automod._state import artifact_path

    try:
        art = json.loads(artifact_path(automod_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(json.dumps({"error": f"cannot read artifact: {e}"}))
        return 1
    review = review_proposal(automod_id, str(art.get("diff", "")), str(art.get("intent", "")))
    print(json.dumps(review, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(_main(sys.argv[1:]))
