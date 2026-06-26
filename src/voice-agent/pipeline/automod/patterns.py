"""Pattern detector for the auto-mod loop (Spec B, Plane 3).

scans turn_telemetry.db for 3 pattern classes:
  - correction repeat (≥3 same signal in `turns.correction_signal`)
  - confab self-flag (≥3 turns with confab_check_state IN
    ('hedged_no_evidence', 'retry_factory_missing') in last CONFAB_WINDOW_DAYS days)
  - tool-gap repeat (deferred — table exists but bucketing logic is
    a future iteration)

On threshold + not already proposed, emits a record to
~/.jarvis/auto-mods/queue.jsonl and stamps proposed_at in the
corresponding tracking table.

Pure read on `turns`; writes only to (a) the new tracking tables (to set
proposed_at) and (b) queue.jsonl. Never raises — returns 0 emitted on any
DB error. Default cadence is set by the caller (jarvis_agent.py
schedules this); this module just provides scan_and_emit().
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import time
from pathlib import Path

from pipeline.automod import criteria
from pipeline.automod._state import queue_path, _automod_home

logger = logging.getLogger("jarvis.automod.patterns")

THRESHOLD = 3
CONFAB_WINDOW_DAYS = 7

# Failure-driven retry: a failed build is re-queued with the failure lesson and
# a directive to try a DIFFERENT, narrower approach. Retries are open-ended by
# default; the 5/day evolution budget is the hard brake, so P0-P3 work keeps
# coming back across days until it produces a functional, reviewable proposal.
MAX_RETRY_ATTEMPTS = None
RETRY_RECENCY_DAYS = 7

# Light root-cause scaffold: collapse near-duplicate correction phrases onto a
# canonical label so related corrections can be grouped later (sub-project A).
# The label is recorded on the intent as `root_cause`; the SQL grouping/dedup is
# unchanged for now. Full embedding/LLM clustering is deferred.
_SYNONYM_MAP = {
    "wordy": "verbosity", "verbose": "verbosity", "shorter": "verbosity",
    "concise": "verbosity", "too long": "verbosity", "brief": "verbosity",
    "rambling": "verbosity",
    "wrong tool": "tool_routing", "use the": "tool_routing",
    "slow": "latency", "took too long": "latency", "faster": "latency",
    "made up": "confabulation", "didnt actually": "confabulation",
    "you lied": "confabulation",
}


def _normalize_signal(s: str) -> str:
    """Canonicalize a correction signal for root-cause grouping. Lowercase,
    strip punctuation, collapse whitespace, then map known synonyms."""
    t = re.sub(r"[^a-z0-9 ]", "", (s or "").lower())
    t = re.sub(r"\s+", " ", t).strip()
    for key, canon in _SYNONYM_MAP.items():
        if key in t:
            return canon
    return t

ERROR_BURST_WINDOW_HOURS = 2
ERROR_BURST_COUNT = 3
ERROR_DRIP_WINDOW_DAYS = 7
ERROR_DRIP_COUNT = 10
ERROR_FIXABILITY_FLOOR = 0.5


def _telemetry_db_path() -> Path:
    p = os.environ.get("JARVIS_TURN_TELEMETRY_DB")
    if p:
        return Path(p)
    home = os.environ.get("JARVIS_HOME") or str(Path.home() / ".local/share/jarvis")
    return Path(home) / "turn_telemetry.db"


def _ensure_queue_dir() -> None:
    queue_path().parent.mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _emit(record: dict) -> None:
    record = criteria.enrich_record(record)
    _ensure_queue_dir()
    line = json.dumps(record, ensure_ascii=False)
    with queue_path().open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    logger.info("[automod] pattern detected: kind=%s id=%s",
                record["kind"], record["id"])


def _next_id(kind: str) -> str:
    suffix = hashlib.sha1(
        f"{kind}-{time.time_ns()}".encode()
    ).hexdigest()[:6]
    return f"automod-{time.strftime('%Y-%m-%d', time.gmtime())}-{suffix}"


def _scan_corrections(conn: sqlite3.Connection) -> int:
    emitted = 0
    rows = conn.execute("""
        SELECT correction_signal, COUNT(*) AS c,
               MIN(ts_utc) AS first_seen, MAX(ts_utc) AS last_seen
          FROM turns
         WHERE correction_signal IS NOT NULL AND correction_signal != ''
      GROUP BY correction_signal
        HAVING c >= ?
    """, (THRESHOLD,)).fetchall()
    for signal, count, first_seen, last_seen in rows:
        existing = conn.execute(
            "SELECT proposed_at FROM recurring_corrections WHERE signal=?",
            (signal,),
        ).fetchone()
        if existing and existing[0]:
            continue
        if existing:
            conn.execute(
                "UPDATE recurring_corrections SET last_seen=?, count=? WHERE signal=?",
                (last_seen, count, signal),
            )
        else:
            conn.execute(
                """INSERT INTO recurring_corrections
                   (signal, first_seen, last_seen, count) VALUES (?, ?, ?, ?)""",
                (signal, first_seen, last_seen, count),
            )
        rec_id = _next_id("correction")
        _emit({
            "id": rec_id,
            "kind": "correction",
            "intent": f"Investigate the recurring correction: {signal!r}. "
                      f"Find the file (likely a prompt under prompts/) where "
                      f"the offending behavior originates and patch it.",
            "rationale": f"user corrected this {count} times "
                         f"({first_seen} → {last_seen})",
            "root_cause": _normalize_signal(signal),
            "evidence": {"signal": signal, "count": count,
                         "first_seen": first_seen, "last_seen": last_seen},
            "created_at": _now_iso(),
        })
        conn.execute(
            "UPDATE recurring_corrections SET proposed_at=? WHERE signal=?",
            (_now_iso(), signal),
        )
        emitted += 1
    conn.commit()
    return emitted


def _scan_confabs(conn: sqlite3.Connection) -> int:
    cutoff = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ",
        time.gmtime(time.time() - CONFAB_WINDOW_DAYS * 86400),
    )
    # The confab_check_state vocabulary is NOT 'save_claim' — that was a
    # design-time assumption that never matched reality. The live DB uses:
    #   hedged_no_evidence — JARVIS made a claim without tool evidence
    #   retry_factory_missing — factory missing during retry (system-side)
    #   caught_t1_passed / caught_t3_passed — caught but recovered (near-miss)
    # Count only the hard-failure states (hedged_no_evidence + retry_factory_missing).
    row = conn.execute(
        """SELECT COUNT(*), MAX(ts_utc) FROM turns
            WHERE confab_check_state IN ('hedged_no_evidence', 'retry_factory_missing')
              AND ts_utc >= ?""",
        (cutoff,),
    ).fetchone()
    count, last_seen = row[0] or 0, row[1]
    if count < THRESHOLD:
        return 0
    signal = f"__confab_failure_window_{CONFAB_WINDOW_DAYS}d__"
    existing = conn.execute(
        "SELECT proposed_at FROM recurring_corrections WHERE signal=?",
        (signal,),
    ).fetchone()
    if existing and existing[0]:
        return 0
    rec_id = _next_id("confab")
    _emit({
        "id": rec_id,
        "kind": "confab",
        "intent": "Investigate recurring confabulation failures. "
                  "JARVIS is making claims without tool evidence "
                  "(hedged_no_evidence) or hitting system-side confab "
                  "gate failures (retry_factory_missing). Likely a "
                  "prompt-strength issue in the supervisor or tool "
                  "descriptions.",
        "rationale": f"{count} confab gate failures in last {CONFAB_WINDOW_DAYS} days",
        "evidence": {"count": count, "last_seen": last_seen,
                     "window_days": CONFAB_WINDOW_DAYS},
        "created_at": _now_iso(),
    })
    if existing:
        conn.execute(
            "UPDATE recurring_corrections SET proposed_at=?, count=?, last_seen=? WHERE signal=?",
            (_now_iso(), count, last_seen, signal),
        )
    else:
        conn.execute(
            """INSERT INTO recurring_corrections
               (signal, first_seen, last_seen, count, proposed_at)
               VALUES (?, ?, ?, ?, ?)""",
            (signal, last_seen, last_seen, count, _now_iso()),
        )
    conn.commit()
    return 1


def _iso_offset(seconds_delta: int) -> str:
    return time.strftime(
        "%Y-%m-%dT%H:%M:%SZ",
        time.gmtime(time.time() + seconds_delta),
    )


def _scan_errors(conn: sqlite3.Connection) -> int:
    """Emit intents for recurring errors that crossed either threshold.

    Burst path: count >= ERROR_BURST_COUNT AND last_seen within
                ERROR_BURST_WINDOW_HOURS hours.
    Drip path:  count >= ERROR_DRIP_COUNT AND last_seen within
                ERROR_DRIP_WINDOW_DAYS days.

    Both gated on fixability_score >= ERROR_FIXABILITY_FLOOR and
    proposed_at IS NULL.

    Spec 2026-05-27 Part 3."""
    # Fallback: if the handler hasn't wired yet, seed from log.
    try:
        from pipeline.automod.error_log_fallback import populate_from_log_if_empty
        populate_from_log_if_empty(conn)
    except Exception as _e:
        logger.debug("[automod] fallback skipped: %s", _e)

    burst_cutoff = _iso_offset(-ERROR_BURST_WINDOW_HOURS * 3600)
    drip_cutoff = _iso_offset(-ERROR_DRIP_WINDOW_DAYS * 86400)

    try:
        rows = conn.execute("""
            SELECT signature, exc_class, exc_message, count,
                   first_seen, last_seen, frames_json, sample_traceback,
                   fixability_score
              FROM recurring_errors
             WHERE proposed_at IS NULL
               AND fixability_score >= ?
               AND (
                    (count >= ? AND last_seen >= ?)
                 OR (count >= ? AND last_seen >= ?)
               )
             ORDER BY count DESC, last_seen DESC
        """, (ERROR_FIXABILITY_FLOOR,
              ERROR_BURST_COUNT, burst_cutoff,
              ERROR_DRIP_COUNT, drip_cutoff)).fetchall()
    except sqlite3.Error as e:
        logger.warning("[automod] _scan_errors query failed: %s", e)
        return 0

    emitted = 0
    for (sig, exc_class, exc_msg, count, first, last,
         frames_json, sample_tb, fixability) in rows:
        rec_id = _next_id("error")
        try:
            frames = json.loads(frames_json or "[]")
        except json.JSONDecodeError:
            frames = []
        frames_text = "\n".join(
            f"  - {f.get('file', '?')}:{f.get('method', '?')}"
            for f in frames
        )
        intent_body = (
            f"Investigate a recurring exception in JARVIS's own code.\n\n"
            f"EXCEPTION: {exc_class}\n"
            f"MESSAGE:   {exc_msg!r}\n"
            f"OCCURRENCES: {count} "
            f"(first seen {first}, last seen {last})\n"
            f"FIXABILITY: {fixability:.2f}\n\n"
            f"AFFECTED FILES (jarvis-owned frames in the traceback):\n"
            f"{frames_text}\n\n"
            f"SAMPLE TRACEBACK:\n"
            f"{sample_tb}\n\n"
            f"INVESTIGATE: read each affected file, identify the root "
            f"cause (may be at any frame in the stack, not just the top), "
            f"and propose a targeted fix. The fix should either prevent "
            f"the exception from being raised OR handle it cleanly when "
            f"it cannot be prevented. Do NOT add a broad except: that "
            f"hides the underlying bug."
        )
        _emit({
            "id": rec_id,
            "kind": "error",
            "intent": intent_body,
            "rationale": (
                f"raised {count} times ({first} → {last}); "
                f"fixability={fixability:.2f}"
            ),
            "evidence": {
                "signature": sig, "exc_class": exc_class,
                "exc_message": exc_msg, "count": count,
                "first_seen": first, "last_seen": last,
                "frames": frames, "fixability_score": fixability,
            },
            "created_at": _now_iso(),
        })
        try:
            conn.execute(
                "UPDATE recurring_errors SET proposed_at=? WHERE signature=?",
                (_now_iso(), sig),
            )
        except sqlite3.Error as e:
            logger.warning("[automod] proposed_at update failed: %s", e)
        emitted += 1
    conn.commit()
    return emitted


def _scan_fitness(conn: sqlite3.Connection) -> int:
    """Emit a proposal for the persistently-weak fitness axis (sub-project A,
    2026-06-23). Reads the evolution ledger (read-only), picks the weak axis via
    fitness_feedback, and emits ONE concrete intent. Deduped via
    recurring_corrections with a synthetic signal, exactly like _scan_confabs.
    Never raises — returns 0 on any error."""
    try:
        from pipeline.automod import fitness_feedback
        from evolution.ledger import read_readings
        # Ledger lives beside turn_telemetry.db; honor the same JARVIS_HOME path.
        ledger_db = _telemetry_db_path().parent / "evolution_ledger.db"
        readings = read_readings(limit=fitness_feedback.LOOKBACK_M, db_path=ledger_db)
    except Exception as e:  # noqa: BLE001
        logger.debug("[automod] fitness scan skipped: %s", e)
        return 0
    hit = fitness_feedback.weak_axis(readings)
    if not hit:
        return 0
    axis, evidence = hit
    built = fitness_feedback.build_intent(axis, evidence)
    if not built:
        return 0
    signal = f"__fitness_axis_{axis}__"
    existing = conn.execute(
        "SELECT proposed_at FROM recurring_corrections WHERE signal=?",
        (signal,),
    ).fetchone()
    if existing and existing[0]:
        return 0
    rec_id = _next_id("fitness")
    _emit({
        "id": rec_id,
        "kind": "fitness",
        "intent": built["intent"],
        "rationale": built["rationale"],
        "root_cause": f"fitness_{axis}",
        "evidence": evidence,
        "created_at": _now_iso(),
    })
    now = _now_iso()
    if existing:
        conn.execute(
            "UPDATE recurring_corrections SET proposed_at=?, count=?, last_seen=? WHERE signal=?",
            (now, evidence["n_below"], now, signal),
        )
    else:
        conn.execute(
            """INSERT INTO recurring_corrections
               (signal, first_seen, last_seen, count, proposed_at)
               VALUES (?, ?, ?, ?, ?)""",
            (signal, now, now, evidence["n_below"], now),
        )
    conn.commit()
    return 1


def _retry_hint(reason: str) -> str:
    r = (reason or "").lower()
    if "too_many_files" in r:
        return ("Your previous diff touched far too many files. Scope this to AT MOST 5 files — "
                "ideally a single file or one prompt edit. If the change is inherently large, pick "
                "the SINGLE highest-leverage file and do only that.")
    if "test" in r:
        return ("Your previous diff broke the test suite. Make a smaller, safer change that keeps "
                "all tests green; run the tests mentally before committing.")
    if "no_commit" in r:
        return ("The previous attempt produced no commit at all. Make a concrete, minimal change "
                "and actually commit it.")
    if "diff_validation" in r or "blocklist" in r:
        return ("The previous diff hit a blocked path. Only edit files under src/voice-agent/ and "
                "never touch the safety/blocklisted files.")
    return "Take a fundamentally different, narrower approach than the previous attempt."


def build_retry_intent(art: dict) -> dict | None:
    """Build a learn-and-retry intent from a failed artifact, with the failure
    lesson + a directive to try a DIFFERENT, narrower approach. Returns None if
    the artifact is ineligible (not failed / fixture).
    Shared by the nightly scanner and the immediate build cycle."""
    if art.get("status") != "failed":
        return None
    ident = str(art.get("lineage") or art.get("id") or "")
    if "test" in ident or "smoke" in ident:
        return None  # don't retry fixtures
    attempt = int(art.get("attempt", 1) or 1)
    reason = str(art.get("rejection_reason", ""))
    lineage = art.get("lineage") or art.get("id")
    original = str(art.get("intent", "")).split("\n\n")[0][:400]
    prior = list(art.get("prior_failures", [])) + [f"attempt {attempt}: {reason}"]
    new_intent = (
        f"RETRY (attempt {attempt + 1}, continue until functional) of a self-evolution "
        f"change that FAILED.\n\n"
        f"GOAL:\n{original}\n\n"
        "PREVIOUS FAILURES — do NOT repeat these approaches:\n"
        + "\n".join(f"- {x}" for x in prior)
        + f"\n\n{_retry_hint(reason)}"
    )
    return {
        "id": _next_id("retry"),
        "kind": art.get("kind") or "retry",
        "intent": new_intent,
        "rationale": f"learn-and-retry after failure: {reason}",
        "lineage": lineage,
        "attempt": attempt + 1,
        "prior_failures": prior,
        "priority": art.get("priority") or "P1",  # inherit the goal's rank
        "root_cause": art.get("root_cause") or "retry",
        "created_at": _now_iso(),
    }


def _scan_failed_retries() -> int:
    """Re-queue failed builds via build_retry_intent. Marks each failed artifact
    `retried` so it is enqueued at most once. Ranked P0-P3 work does not age
    out; unranked stale artifacts are ignored to avoid reviving old fixtures.
    Never raises."""
    cutoff = time.time() - RETRY_RECENCY_DAYS * 86400
    emitted = 0
    try:
        artifacts = sorted(_automod_home().glob("automod-*.json"))
    except Exception:  # noqa: BLE001
        return 0
    for p in artifacts:
        try:
            art = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if art.get("retried"):
            continue
        created = str(art.get("created_at", ""))
        try:
            priority = str(art.get("priority", "")).upper()
            stale = created and time.mktime(time.strptime(created[:19], "%Y-%m-%dT%H:%M:%S")) < cutoff
            if stale and priority not in {"P0", "P1", "P2", "P3"}:
                continue  # too old to chase
        except (ValueError, TypeError):
            pass
        intent = build_retry_intent(art)
        if not intent:
            continue
        _emit(intent)
        try:
            art["retried"] = True
            p.write_text(json.dumps(art, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
        emitted += 1
    return emitted


def scan_and_emit() -> int:
    """Scan all pattern classes; emit intents that crossed threshold.
    Returns total intents emitted across all classes."""
    db = _telemetry_db_path()
    if not db.exists():
        logger.debug("[automod] telemetry db missing: %s", db)
        return 0
    emitted = 0
    try:
        with sqlite3.connect(str(db)) as conn:
            emitted += _scan_corrections(conn)
            emitted += _scan_confabs(conn)
            emitted += _scan_errors(conn)   # NEW (auto-mod error-driven, Spec 2026-05-27)
            emitted += _scan_fitness(conn)  # NEW (per-axis fitness feedback, sub-project A)
            emitted += _scan_failed_retries()  # NEW (learn-and-retry failed builds)
    except sqlite3.Error as e:
        logger.warning("[automod] scan failed: %s: %s",
                       type(e).__name__, e)
        return 0
    if emitted:
        logger.info("[automod] emitted %d intent(s) this scan", emitted)
    return emitted


def collapse_failed_retries(*, archive: bool = True) -> int:
    """Collapse a goal's per-attempt FAILED records into one. The retry mechanism
    writes a separate failed artifact per attempt, so a goal that failed N times
    leaves N records that read as duplicates in the /evolution Failed list. Keeps
    the lowest-attempt record per lineage (it carries the real goal text — retries
    only say 'RETRY attempt N'); archives the rest under _superseded/ (reversible).
    Returns the count collapsed. Best-effort; never raises."""
    home = _automod_home()
    by_lineage: dict[str, list] = {}
    try:
        for f in home.glob("*.json"):
            if f.name.endswith(".review.json"):
                continue
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if d.get("status") != "failed":
                continue
            lin = d.get("lineage") or d.get("parent_sha") or d.get("id")
            by_lineage.setdefault(lin, []).append((f, d))
    except OSError:
        return 0
    arch = home / "_superseded"
    collapsed = 0
    for recs in by_lineage.values():
        if len(recs) <= 1:
            continue
        recs.sort(key=lambda fd: int(fd[1].get("attempt", 0) or 0))
        for f, d in recs[1:]:  # keep the original (lowest attempt); supersede the rest
            rid = d.get("id") or f.stem
            for ext in (".json", ".review.json", ".log", ".intent.txt"):
                sib = home / f"{rid}{ext}"
                if not sib.exists():
                    continue
                try:
                    if archive:
                        arch.mkdir(exist_ok=True)
                        sib.rename(arch / sib.name)
                    else:
                        sib.unlink()
                except OSError:
                    continue
            collapsed += 1
    if collapsed:
        logger.info("[patterns] collapsed %d redundant retry-failure record(s)", collapsed)
    return collapsed
