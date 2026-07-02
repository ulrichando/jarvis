"""JARVIS self-assessment / introspection (2026-06-23).

The reflective layer of the evolution loop: JARVIS looks at his own evidence —
weak fitness axes, recurring corrections, confab failures, failed build reasons —
and reasons about WHAT HIS FLAWS ARE and WHAT HE SHOULD IMPROVE. One out-of-band
Anthropic call (the webcam.py pattern); result stored to disk and surfaced in the
web /evolution console, where each improvement can be queued as a proposal.

Never raises into the caller — returns a structured payload with an `error` key
when a model/key is unavailable. Off the turn path (background / on-demand).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path

from pipeline.automod._state import _automod_home, queue_path

logger = logging.getLogger("jarvis.automod.introspection")

DEFAULT_MODEL = "claude-sonnet-4-6"


def _assessment_path() -> Path:
    return _automod_home() / "self_assessment.json"


def _model() -> str:
    return os.environ.get("JARVIS_INTROSPECTION_MODEL", DEFAULT_MODEL)


_FAILURE_CLASSES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("blocklist", ("blocklist", "blocked_path", "plan_rejected")),
    ("council_block", ("council_block",)),
    ("tests_failed", ("tests_failed", "base_suite", "test")),
    ("no_commit", ("no_commit",)),
    ("too_many_files", ("too_many_files",)),
)

_PATH_RE = re.compile(r"src/[\w./-]+\.\w+")
_ERROR_LINE_RE = re.compile(r"(?:Error|Exception|Traceback|FAILED|assert)", re.IGNORECASE)


def classify_failure(reason: str) -> str:
    r = (reason or "").lower()
    for label, needles in _FAILURE_CLASSES:
        if any(n in r for n in needles):
            return label
    return "other"


def extract_failure_digest(failed: list[dict]) -> dict:
    """Structured digest of failed builds (AutoData lesson: structure the
    trajectories BEFORE reasoning over them — outcome codes alone hide
    systematic patterns like 'the builder keeps targeting blocklisted paths').
    Pure function; items are {id, reason, intent, attempt, log_tail}."""
    by_class: dict[str, int] = {}
    path_counts: dict[str, int] = {}
    error_lines: list[str] = []
    seen_errors: set[str] = set()
    self_loop = 0
    from pipeline.automod.patterns import _SELF_LOOP_RE
    for item in failed:
        reason = str(item.get("reason") or "")
        cls = classify_failure(reason)
        by_class[cls] = by_class.get(cls, 0) + 1
        blob = f"{item.get('intent') or ''} {reason}"
        for path in _PATH_RE.findall(blob):
            path_counts[path] = path_counts.get(path, 0) + 1
        if _SELF_LOOP_RE.search(str(item.get("intent") or "")[:240]):
            self_loop += 1
        for line in str(item.get("log_tail") or "").splitlines():
            line = line.strip()
            if _ERROR_LINE_RE.search(line) and line[:60] not in seen_errors:
                seen_errors.add(line[:60])
                error_lines.append(line[:200])
    repeated_paths = sorted(
        ((p, c) for p, c in path_counts.items() if c >= 2),
        key=lambda pc: -pc[1],
    )
    return {
        "n_failed": len(failed),
        "by_class": by_class,
        "repeated_target_paths": [{"path": p, "count": c} for p, c in repeated_paths[:6]],
        "self_loop_targets": self_loop,
        "sample_error_lines": error_lines[:5],
    }


def gather_failure_digest(limit: int = 15) -> dict | None:
    """IO wrapper: newest `limit` failed artifacts + their build-log tails →
    extract_failure_digest. Best-effort; None when nothing failed."""
    from pipeline.automod._state import artifact_log_path
    items: list[dict] = []
    try:
        paths = sorted(_automod_home().glob("automod-*.json"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return None
    for p in paths:
        if p.name.endswith(".review.json") or len(items) >= limit:
            continue
        try:
            art = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if art.get("status") != "failed":
            continue
        tail = ""
        try:
            log = artifact_log_path(str(art.get("id") or p.stem))
            if log.exists():
                tail = log.read_text(encoding="utf-8", errors="replace")[-4000:]
        except OSError:
            pass
        items.append({"id": art.get("id"), "reason": art.get("rejection_reason"),
                      "intent": art.get("intent"), "attempt": art.get("attempt", 1),
                      "log_tail": tail})
    return extract_failure_digest(items) if items else None


def gather_evidence() -> dict:
    """Collect the self-reflection evidence bundle from existing signals."""
    evidence: dict = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

    # Per-axis fitness + weak axis.
    try:
        from evolution.ledger import read_readings, DEFAULT_LEDGER_DB
        rows = read_readings(limit=5, db_path=DEFAULT_LEDGER_DB)
        if rows:
            per_axis = rows[0].get("per_axis", {}) or {}
            evidence["per_axis_latest"] = per_axis
            evidence["composite_latest"] = rows[0].get("composite")
            # Pre-compute the weak/strong axis so the model can't invert the
            # scale (every axis is GOODNESS in [0,1]; 1.0 = perfect).
            if per_axis:
                ranked = sorted(per_axis.items(), key=lambda kv: kv[1])
                evidence["weakest_axis"] = {"axis": ranked[0][0], "score": round(ranked[0][1], 3)}
                evidence["strongest_axis"] = {"axis": ranked[-1][0], "score": round(ranked[-1][1], 3)}
    except Exception as e:  # noqa: BLE001
        logger.debug("introspection: ledger read failed: %s", e)

    # Recent failed-build reasons (so JARVIS sees why his own changes failed).
    failed: list[dict] = []
    for p in sorted(_automod_home().glob("automod-*.json"))[-20:]:
        try:
            a = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if a.get("status") == "failed":
            failed.append({"id": a.get("id"), "reason": a.get("rejection_reason"),
                           "intent": str(a.get("intent", ""))[:160]})
    evidence["recent_failed_builds"] = failed[-8:]

    # Structured failure digest over the same artifacts + their build logs —
    # gives the model failure CLASSES and repeated targets, not just codes.
    try:
        digest = gather_failure_digest()
        if digest:
            evidence["failure_digest"] = digest
    except Exception as e:  # noqa: BLE001
        logger.debug("introspection: failure digest failed: %s", e)

    # Recurring corrections / confab signals from telemetry (best-effort).
    try:
        import sqlite3
        from pipeline.automod.patterns import _telemetry_db_path
        db = _telemetry_db_path()
        if db.exists():
            with sqlite3.connect(str(db)) as conn:
                corr = conn.execute(
                    "SELECT correction_signal, COUNT(*) c FROM turns "
                    "WHERE correction_signal IS NOT NULL AND correction_signal!='' "
                    "GROUP BY correction_signal ORDER BY c DESC LIMIT 10"
                ).fetchall()
                evidence["recurring_corrections"] = [{"signal": s, "count": c} for s, c in corr]
                conf = conn.execute(
                    "SELECT COUNT(*) FROM turns WHERE confab_check_state IN "
                    "('hedged_no_evidence','retry_factory_missing')"
                ).fetchone()
                evidence["confab_failures"] = conf[0] if conf else 0
    except Exception as e:  # noqa: BLE001
        logger.debug("introspection: telemetry read failed: %s", e)

    return evidence


_PROMPT = """You are JARVIS, a voice-first AI assistant, performing an honest \
self-assessment of your own flaws. Below is real evidence from your telemetry and \
self-modification loop.

EVIDENCE (JSON):
{evidence}

CRITICAL — how to read the axes: every per-axis score is a GOODNESS value in \
[0,1] where 1.0 is PERFECT and LOW is BAD. Do NOT invert them — a confab score of \
0.99 means you almost never confabulate (excellent), NOT that you confabulate 99% \
of the time. `weakest_axis` is your real problem area; `strongest_axis` is healthy. \
The axes: reask = how reliably you avoid re-asking; confab = how reliably you avoid \
claiming success without evidence; latency = speed; action = clean tool-use; \
interruption = barge-in handling.

Reflect honestly. Identify your most important FLAWS and concrete IMPROVEMENTS you \
should make to your own prompts/code. Be specific and actionable — each improvement \
should be something a coding agent could implement. Respond with ONLY valid JSON:

{{"summary": "<2-3 sentence honest self-assessment>",
  "flaws": [{{"area": "<short>", "detail": "<what's wrong, grounded in the evidence>"}}],
  "improvements": [{{"title": "<imperative, specific>", "rationale": "<why, from evidence>", "target_axis": "<reask|confab|latency|action|interruption|none>"}}]}}"""


def _parse_json_object(text: str) -> dict | None:
    """Parse a JSON object out of model output, tolerating prose / ```json fences."""
    if not text:
        return None
    for candidate in (
        text,
        # strip a leading ```json / ``` fence and trailing fence
        re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE),
    ):
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    # last resort: grab the outermost {...} span
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            return None
    return None


def run_self_assessment() -> dict:
    """Gather evidence, ask the model to self-critique, store + return the result."""
    evidence = gather_evidence()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return {"error": "no ANTHROPIC_API_KEY", "evidence": evidence}
    text = ""
    try:
        import anthropic
        client = anthropic.Anthropic(timeout=45.0, max_retries=1)
        resp = client.messages.create(
            model=_model(),
            max_tokens=3000,
            messages=[{"role": "user",
                       "content": _PROMPT.format(evidence=json.dumps(evidence, indent=2))}],
        )
        text = "".join(getattr(b, "text", "") for b in resp.content).strip()
    except Exception as e:  # noqa: BLE001
        logger.warning("introspection: model call failed: %s", e)
        return {"error": f"model call failed: {e}", "model": _model(), "evidence": evidence}

    parsed = _parse_json_object(text)
    if parsed is None:
        logger.warning("introspection: could not parse model JSON; raw head=%r", text[:200])
        return {"error": "could not parse model output as JSON",
                "raw": text[:500], "model": _model(), "evidence": evidence}

    result = {
        "summary": str(parsed.get("summary", "")).strip(),
        "flaws": parsed.get("flaws", [])[:8],
        "improvements": parsed.get("improvements", [])[:8],
        "model": _model(),
        "generated_at": evidence["generated_at"],
        "evidence": evidence,
    }
    # Closed loop: queue the improvements as proposals (deduped).
    try:
        result["queued"] = enqueue_improvements(result)
    except Exception as e:  # noqa: BLE001
        logger.debug("introspection: enqueue failed: %s", e)
        result["queued"] = 0

    try:
        p = _assessment_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        from pipeline.automod import artifact
        artifact.audit("automod_self_assessment", flaws=len(result["flaws"]),
                       improvements=len(result["improvements"]), queued=result.get("queued", 0))
    except Exception as e:  # noqa: BLE001
        logger.debug("introspection: store failed: %s", e)
    return result


def read_self_assessment() -> dict | None:
    """Return the last stored self-assessment, or None."""
    try:
        return json.loads(_assessment_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def enqueue_improvements(result: dict, *, max_items: int = 3) -> int:
    """Queue the assessment's improvements as automod intents (the closed loop:
    self-assessment → queued proposals). Admission is the shared
    patterns.queue_admission gate: exact + PARAPHRASE dedup against the live
    queue and recent artifacts (LLM re-phrasings of an already-attempted goal
    were the June duplicate storm), and the self-loop filter (goals targeting
    the blocklisted automod pipeline route to needs-human.jsonl — the
    assessment's favourite unbuildable suggestion). Returns how many queued."""
    from pipeline.automod import criteria, patterns
    improvements = result.get("improvements") or []
    if not improvements:
        return 0
    qp = queue_path()
    qp.parent.mkdir(parents=True, exist_ok=True)
    queued = 0
    for im in improvements[:max_items]:
        title = str(im.get("title", "")).strip()
        if not title:
            continue
        rationale = str(im.get("rationale", "")).strip() or "From JARVIS self-assessment."
        axis = str(im.get("target_axis", "") or "general")
        suffix = hashlib.sha1(f"{title}-{time.time_ns()}".encode()).hexdigest()[:6]
        rec = criteria.enrich_record({
            "id": f"automod-{time.strftime('%Y-%m-%d', time.gmtime())}-{suffix}",
            "kind": "self_improvement",
            "intent": f"{title}\n\n{rationale}",
            "rationale": rationale,
            "root_cause": f"improve_{axis}",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })
        admit, reason = patterns.queue_admission(rec)
        if not admit:
            logger.info("[introspection] improvement rejected (%s): %s",
                        reason, title[:80])
            continue
        with qp.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        queued += 1
    if queued:
        logger.info("[introspection] queued %d improvement(s) from self-assessment", queued)
    return queued


if __name__ == "__main__":
    print(json.dumps(run_self_assessment(), indent=2, ensure_ascii=False))
