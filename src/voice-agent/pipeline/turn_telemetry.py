"""SQLite turn telemetry. Non-blocking writes; write failures are
logged (throttled) rather than silently dropped — blind metrics make
debugging impossible (see .claude/rules/voice-agent.md).

Every JARVIS turn writes one row. Reading is via `--report` (optionally
scoped with `--days N`).
"""
from __future__ import annotations

import logging
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("jarvis.telemetry")

# Throttle counter for log_turn write failures: log the first one loudly,
# then every 100th, so a persistent fault (disk full, locked DB) surfaces
# without spamming a line on every turn.
_log_turn_fail_count = 0

# Acceptance threshold for time-to-first-word, in milliseconds. Defaults
# to 1000ms (the spec target). Configurable so the CI / dogfood signal
# can tighten or loosen without code changes.
TTFW_TARGET_MS = int(os.environ.get("JARVIS_TTFW_TARGET_MS", "1000"))

# A route receiving fewer than this fraction of recent turns flags as
# "under-served" — typically a sign the classifier collapsed onto a
# single route (e.g. always TASK). Spec calls this out as an acceptance
# signal: "no route receives <5% of total traffic".
ROUTE_HEALTH_FLOOR = 0.05

# All routes the classifier is supposed to produce. Used by the health
# check to tell us about routes that produced ZERO traffic in the
# window — those won't show up in a GROUP BY otherwise. As of
# 2026-05-24, TASK was split into 5 sub-routes (DESKTOP/BROWSER/CODE/
# FILES/OTHER); the health check's 5% floor will naturally flag any
# sub-route that doesn't get traffic, which is the intended signal.
ALL_ROUTES = (
    "BANTER",
    "TASK_DESKTOP", "TASK_BROWSER", "TASK_CODE", "TASK_FILES", "TASK_OTHER",
    "REASONING", "EMOTIONAL",
)

# Pre-TTS confab gate state values (2026-05-24).
# Stored in turns.confab_check_state as TEXT (no schema enforcement).
# Convention only; documented for type-checkers + tests.
CONFAB_STATE_CLEAN              = "clean"
CONFAB_STATE_CAUGHT_T1_PASSED   = "caught_t1_passed"
CONFAB_STATE_CAUGHT_T2_PASSED   = "caught_t2_passed"
CONFAB_STATE_CAUGHT_T3_PASSED   = "caught_t3_passed"
CONFAB_STATE_CAUGHT_FILLER      = "caught_filler"
CONFAB_STATE_BYPASSED_KILLED    = "bypassed_killed"

# Precise sub-states for the gate's "clean" (no-retry) verdicts — added
# 2026-05-27 to make the four bypass reasons distinguishable in the DB
# instead of collapsing them into one indistinguishable CLEAN value.
CONFAB_STATE_CLEAN_BYPASS_ROUTE   = "clean_bypass_route"     # BANTER / EMOTIONAL
CONFAB_STATE_CLEAN_UNKNOWN_ROUTE  = "clean_unknown_route"    # route not TASK_* / REASONING
CONFAB_STATE_CLEAN_NO_CLAIM       = "clean_no_claim"         # text didn't trip any pattern
CONFAB_STATE_CLEAN_TOOL_CALLED    = "clean_tool_called"      # tool_calls non-empty (genuine action)

# New failure-precision states when the gate trips but retry can't run cleanly.
CONFAB_STATE_RETRY_FACTORY_MISSING = "retry_factory_missing"  # gate tripped, _jarvis_pre_tts_llm_factory was None
CONFAB_STATE_RETRY_EXCEPTION       = "retry_exception"        # retry chain raised — see logs

# Post-tool reply-required gate states (2026-05-27). Stored in
# turns.confab_check_state. These mirror the confab cascade but for
# the inverse failure: tool fired but no text reply was voiced.
#   _T1_PASSED: tier 1 (retry) produced text
#   _T2_PASSED: tier 2 (escalate) produced text
#   _T3_PASSED: tier 3 (cross_provider) produced text
#   _FILLER:    all tiers exhausted — safe filler voiced
CONFAB_STATE_NO_TEXT_T1_PASSED  = "no_text_t1_passed"
CONFAB_STATE_NO_TEXT_T2_PASSED  = "no_text_t2_passed"
CONFAB_STATE_NO_TEXT_T3_PASSED  = "no_text_t3_passed"
CONFAB_STATE_NO_TEXT_FILLER     = "no_text_filler"

DEFAULT_DB_PATH = Path(
    os.environ.get(
        "JARVIS_TELEMETRY_PATH",
        Path.home() / ".local" / "share" / "jarvis" / "turn_telemetry.db",
    )
).expanduser()

# Base schema — does NOT include `subagent`. That column is added
# afterwards by the online migration so a pre-Phase-6 db that already
# has the table (without the column) doesn't trip on a CREATE INDEX
# referencing a column that hasn't been migrated in yet.
_BASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY,
    ts_utc TEXT NOT NULL,
    user_text TEXT NOT NULL,
    jarvis_text TEXT NOT NULL,
    emotion TEXT,
    route TEXT,
    llm_used TEXT,
    voice_used TEXT,
    ttfw_ms INTEGER,
    total_audio_ms INTEGER,
    user_followup_30s INTEGER,
    route_fallback INTEGER,
    notes TEXT,
    memory_auto_extracted INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_turns_ts_utc ON turns(ts_utc);
CREATE INDEX IF NOT EXISTS idx_turns_route  ON turns(route);

-- Phase 10.6 — launch_app outcome ledger. One row per launch attempt
-- across all sessions. Lets the report surface per-binary OK / MISSING
-- / CRASHED counts so we can spot patterns like "users keep asking for
-- 'notepad' but it isn't installed → suggest adding mousepad to the
-- subagent's app-name lookup".
CREATE TABLE IF NOT EXISTS launch_attempts (
    id INTEGER PRIMARY KEY,
    ts_utc TEXT NOT NULL,
    binary TEXT NOT NULL,
    outcome TEXT NOT NULL  -- 'OK' | 'MISSING' | 'CRASHED'
);
CREATE INDEX IF NOT EXISTS idx_launch_ts ON launch_attempts(ts_utc);
CREATE INDEX IF NOT EXISTS idx_launch_binary ON launch_attempts(binary);
"""


def init_db(db_path: Path = DEFAULT_DB_PATH) -> None:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        # Step 1 — base schema (works for fresh dbs AND no-op for old ones).
        conn.executescript(_BASE_SCHEMA)
        # Step 2-onwards — online migrations: ALTER TABLE ADD COLUMN IF
        # NOT EXISTS isn't supported by SQLite, so we check first and
        # tolerate the dup-column race that an exception would suggest.
        cols = {r[1] for r in conn.execute("PRAGMA table_info(turns)")}
        if "subagent" not in cols:
            try:
                conn.execute("ALTER TABLE turns ADD COLUMN subagent TEXT")
            except sqlite3.OperationalError:
                pass
        # One-time backfill for the specialist → subagent rename. The
        # rename was done by ADD COLUMN (SQLite can't RENAME), so old
        # DBs end up with the populated `specialist` column AND an
        # empty `subagent` column — `report()` queries only `subagent`
        # post-rename, so all pre-rename rows silently disappear from
        # the soak-rescore + per-subagent breakdowns. Copy the value
        # over for rows that still have the old column populated.
        # Idempotent because of the `subagent IS NULL` guard: after
        # the first run every row has subagent set, future calls find
        # nothing to update. Guarded on the `specialist` column
        # existing — fresh DBs never had it and skip cleanly.
        cols = {r[1] for r in conn.execute("PRAGMA table_info(turns)")}
        if "specialist" in cols:
            conn.execute(
                "UPDATE turns SET subagent = specialist "
                "WHERE subagent IS NULL AND specialist IS NOT NULL"
            )
        if "interrupted" not in cols:
            # Phase 10.5 — bool flag; stamped True if the user barged
            # in, fired a kill-phrase, or the framework auto-interrupted
            # this turn. Lets the report show per-route interrupt-rate
            # for tuning the per-route + per-emotion overlay.
            try:
                conn.execute(
                    "ALTER TABLE turns ADD COLUMN interrupted INTEGER DEFAULT 0"
                )
            except sqlite3.OperationalError:
                pass
        # 2026-05-05 — cost-tracker columns ported from claude-code's
        # cost-tracker.ts. Per-turn token + cost accounting so the
        # operator can see "this 10-min session burned X tokens at
        # $Y/M". `context_pressure` is the pre-flight state ("ok"/
        # "warn"/"hard") at turn start — lets us correlate failures
        # with context-window pressure.
        if "input_tokens" not in cols:
            try:
                conn.execute("ALTER TABLE turns ADD COLUMN input_tokens INTEGER")
            except sqlite3.OperationalError:
                pass
        if "output_tokens" not in cols:
            try:
                conn.execute("ALTER TABLE turns ADD COLUMN output_tokens INTEGER")
            except sqlite3.OperationalError:
                pass
        if "cost_usd" not in cols:
            try:
                conn.execute("ALTER TABLE turns ADD COLUMN cost_usd REAL")
            except sqlite3.OperationalError:
                pass
        if "context_pressure" not in cols:
            try:
                conn.execute("ALTER TABLE turns ADD COLUMN context_pressure TEXT")
            except sqlite3.OperationalError:
                pass
        # Phase 2 memory reliability — bool flag stamped True when the
        # per-turn memory extractor ran via the auto-extraction path
        # (regex / heuristic), False when it ran via LLM-extraction or
        # wasn't triggered. Lets us compute auto-extraction rate over time.
        # Anthropic prompt-cache hit count (global review §P0-17). Zero
        # for providers without caching; non-zero confirms livekit-plugins-
        # anthropic's `caching="ephemeral"` kwarg is actually firing.
        if "prompt_cached_tokens" not in cols:
            try:
                conn.execute(
                    "ALTER TABLE turns ADD COLUMN prompt_cached_tokens INTEGER DEFAULT 0"
                )
            except sqlite3.OperationalError:
                pass
        if "memory_auto_extracted" not in cols:
            try:
                conn.execute(
                    "ALTER TABLE turns ADD COLUMN memory_auto_extracted INTEGER DEFAULT 0"
                )
            except sqlite3.OperationalError:
                pass
        # 2026-05-18 — which browser backend the browser subagent ran
        # on this turn: 'ext' (Chrome extension) or 'cdp' (Playwright
        # bundled Chromium fallback) or NULL (no browser subagent this
        # turn). Lets the operator audit how often CDP fallback fires
        # — high rate means the user's Chrome extension is misconfigured
        # and the fix is non-degradation rather than just CDP routing.
        if "browser_backend" not in cols:
            try:
                conn.execute(
                    "ALTER TABLE turns ADD COLUMN browser_backend TEXT"
                )
            except sqlite3.OperationalError:
                pass
        # 2026-05-18 — computer_use subagent telemetry. Two scalar
        # columns on `turns` (per-turn step count + cost), plus a
        # full audit table for per-action records. Spec:
        # docs/superpowers/specs/2026-05-18-jarvis-computer-use-parity-design.md
        if "computer_use_steps" not in cols:
            try:
                conn.execute(
                    "ALTER TABLE turns ADD COLUMN computer_use_steps INTEGER"
                )
            except sqlite3.OperationalError:
                pass
        if "computer_use_cost_usd" not in cols:
            try:
                conn.execute(
                    "ALTER TABLE turns ADD COLUMN computer_use_cost_usd REAL"
                )
            except sqlite3.OperationalError:
                pass
        # Audit table — one row per computer_use_loop action.
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS computer_use_actions (
                id INTEGER PRIMARY KEY,
                ts_utc TEXT NOT NULL,
                handoff_id TEXT NOT NULL,
                step INTEGER NOT NULL,
                model_used TEXT,
                action TEXT NOT NULL,
                params_json TEXT,
                success INTEGER NOT NULL,
                screenshot_path TEXT,
                notes TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_cua_handoff
                ON computer_use_actions(handoff_id);
            CREATE INDEX IF NOT EXISTS idx_cua_ts
                ON computer_use_actions(ts_utc);
        """)
        # 2026-05-18 — pwd_check_state per-action audit. Lets the operator
        # query fast-path/slow-path/fail-open ratios over time and alert
        # when fail-open exceeds the soak-acceptable threshold (~5%).
        # Spec: docs/superpowers/specs/2026-05-18-cua-password-check-failopen-design.md
        cua_cols = {
            r[1] for r in conn.execute(
                "PRAGMA table_info(computer_use_actions)"
            )
        }
        if "pwd_check_state" not in cua_cols:
            try:
                conn.execute(
                    "ALTER TABLE computer_use_actions ADD COLUMN pwd_check_state TEXT"
                )
            except sqlite3.OperationalError:
                pass
        # 2026-05-19 — confab_check_state per-turn audit. Tracks the
        # defense-in-depth verdict (evidence_ok / hedged_no_evidence /
        # refused_handoff / stale_ctx_dropped / unchecked). Spec:
        # docs/superpowers/specs/2026-05-19-confab-defense-in-depth-design.md §5.4
        turn_cols = {
            r[1] for r in conn.execute("PRAGMA table_info(turns)")
        }
        if "confab_check_state" not in turn_cols:
            try:
                conn.execute(
                    "ALTER TABLE turns ADD COLUMN confab_check_state TEXT"
                )
            except sqlite3.OperationalError:
                pass
        # 2026-05-24 — pre-TTS confab gate observability columns.
        # confab_pattern_matched: which _STRONG_CLAIMS regex source string
        # fired the gate (e.g. r"\b(?:chrome|firefox|...|open|launched|running)\b").
        # confab_retry_models: JSON list of model ids tried in order, ending
        # with the model whose reply was voiced (or empty when gate didn't
        # trip). Both NULL when JARVIS_PRE_TTS_CONFAB_GATE=0 / gate bypass.
        # Spec: docs/superpowers/specs/2026-05-24-pre-tts-confab-gate-design.md
        gate_cols = {
            r[1] for r in conn.execute("PRAGMA table_info(turns)")
        }
        for col, decl in (
            ("confab_pattern_matched", "TEXT"),
            ("confab_retry_models",    "TEXT"),
        ):
            if col not in gate_cols:
                try:
                    conn.execute(
                        f"ALTER TABLE turns ADD COLUMN {col} {decl}"
                    )
                except sqlite3.OperationalError:
                    pass
        # 2026-05-19 — echo-cancellation cascade per-turn audit. Six
        # columns: which AEC layers were active, the detected output
        # profile, and the L2 delay / L3 latency observed. Written from
        # the agent by reading ~/.jarvis/aec-state.json (the AEC runs in
        # the voice-client process). Spec: 2026-05-19-echo-cancellation-cascade-design.md §5.5
        aec_cols = {r[1] for r in conn.execute("PRAGMA table_info(turns)")}
        for col, decl in (
            ("aec_layer1_active",     "INTEGER"),
            ("aec_layer2_aec_active", "INTEGER"),
            ("aec_layer3_active",     "INTEGER"),
            ("output_profile",        "TEXT"),
            ("apm_delay_ms_p50",      "INTEGER"),
            ("dtln_latency_ms_p95",   "REAL"),
        ):
            if col not in aec_cols:
                try:
                    conn.execute(f"ALTER TABLE turns ADD COLUMN {col} {decl}")
                except sqlite3.OperationalError:
                    pass
        # 2026-05-24 — memory + procedure loop observability columns (Spec A).
        # save_trigger_fired / recall_trigger_fired: was the regex trigger in
        # jarvis_agent.on_user_turn_completed hit on this turn (1) or not (0)?
        # procedure_match_offered: did Track 2.5 append a "Want me to keep
        # these steps as 'X'?" offer to this reply?
        # procedure_match_executed: did the user confirm and procedure apply?
        # tool_call_count / had_tool_error: feed the Track 2.5 success-gate
        # check on the next autonomous review pass.
        # Spec: docs/superpowers/specs/2026-05-24-jarvis-memory-and-procedure-loop-design.md
        memory_loop_cols = {r[1] for r in conn.execute("PRAGMA table_info(turns)")}
        for col, decl in (
            ("save_trigger_fired",       "INTEGER DEFAULT 0"),
            ("recall_trigger_fired",     "INTEGER DEFAULT 0"),
            ("procedure_match_offered",  "INTEGER DEFAULT 0"),
            ("procedure_match_executed", "INTEGER DEFAULT 0"),
            ("tool_call_count",          "INTEGER DEFAULT 0"),
            ("had_tool_error",           "INTEGER DEFAULT 0"),
        ):
            if col not in memory_loop_cols:
                try:
                    conn.execute(f"ALTER TABLE turns ADD COLUMN {col} {decl}")
                except sqlite3.OperationalError:
                    pass
        # 2026-05-24 — Spec B (Plane 3) — auto-mod pattern tracking.
        # correction_signal: lowercase form of any user correction in the
        # turn ("stop saying sir", "too verbose"). NULL when no correction
        # detected. Populated by the autonomous review path (B-T12 wires
        # the extractor in skill_review.autonomous_review_turn).
        automod_cols = {r[1] for r in conn.execute("PRAGMA table_info(turns)")}
        if "correction_signal" not in automod_cols:
            try:
                conn.execute("ALTER TABLE turns ADD COLUMN correction_signal TEXT")
            except sqlite3.OperationalError:
                pass
        # 2026-05-27 — subagent dispatch telemetry (Plan:
        # docs/superpowers/plans/2026-05-27-voice-agent-subagent-dispatch.md).
        # subagent_type: which subagent profile owned this turn
        # ('explore' / 'plan' / 'edit' / 'verify' / 'review' / etc.) or
        # NULL when no subagent dispatch fired. subagent_ms: total
        # wall-clock the subagent loop ran (start of dispatch → final
        # tool_result). subagent_status: 'success' / 'failure' /
        # 'timeout' / 'cancelled'. All NULL on turns that didn't go
        # through subagent dispatch — a GROUP BY counts only meaningful
        # rows.
        try:
            cur = conn.cursor()
            cur.execute("ALTER TABLE turns ADD COLUMN subagent_type TEXT")
        except sqlite3.OperationalError:
            pass  # column already exists
        try:
            cur = conn.cursor()
            cur.execute("ALTER TABLE turns ADD COLUMN subagent_ms INTEGER")
        except sqlite3.OperationalError:
            pass
        try:
            cur = conn.cursor()
            cur.execute("ALTER TABLE turns ADD COLUMN subagent_status TEXT")
        except sqlite3.OperationalError:
            pass
        # 2026-05-28 — French/English code-switch feature. Detected user
        # language per turn ('en' / 'fr'). Default 'en' so existing rows
        # and callers that don't pass user_lang stay back-compat. Lets us
        # spot code-switch patterns at the SQL level without trawling
        # transcripts.
        try:
            conn.execute(
                "ALTER TABLE turns ADD COLUMN user_lang TEXT DEFAULT 'en'"
            )
        except sqlite3.OperationalError:
            pass  # column already exists — idempotent on every startup
        # Two pattern tables — populated by pipeline.automod.patterns, drained
        # by the spawner. proposed_at IS NULL means "not yet emitted to queue".
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS recurring_corrections (
                signal TEXT PRIMARY KEY,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 1,
                proposed_at TEXT,
                resolved_at TEXT
            );
            CREATE TABLE IF NOT EXISTS tool_gap_patterns (
                intent_hash TEXT PRIMARY KEY,
                canonical_intent TEXT NOT NULL,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 1,
                sample_tools_json TEXT,
                proposed_at TEXT,
                resolved_at TEXT
            );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_turns_subagent ON turns(subagent)")
        # Auto-mod error-driven branch (Spec 2026-05-27). Idempotent.
        # Populated by pipeline/automod/error_logger.ErrorTelemetryHandler;
        # read by pipeline/automod/patterns._scan_errors.
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS recurring_errors (
                signature TEXT PRIMARY KEY,
                exc_class TEXT NOT NULL,
                exc_message TEXT,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 1,
                frames_json TEXT NOT NULL,
                sample_traceback TEXT,
                fixability_score REAL DEFAULT 0.5,
                proposed_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_recurring_errors_last_seen
                ON recurring_errors(last_seen);
        """)
        # 2026-05-30 — browser_task per-step trace (Web-Nav Phase 1, Task 4).
        # One row per browser_use Agent step surfaced from
        # browser_use_bridge/runner.py via tools/browser.py, so a failed
        # browser_task is debuggable post-mortem (which action, which step,
        # did it succeed). Additive only — mirrors the computer_use_actions
        # audit pattern; the `turns` schema is untouched. Plan:
        # docs/superpowers/plans/2026-05-30-web-nav-p1-routing-reliability-observability.md
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS browser_task_steps (
                id INTEGER PRIMARY KEY,
                ts_utc TEXT NOT NULL,
                task TEXT NOT NULL,
                step_index INTEGER NOT NULL,
                action TEXT,
                ok INTEGER NOT NULL,
                detail TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_bts_ts
                ON browser_task_steps(ts_utc);
        """)


def log_turn(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    user_text: str,
    jarvis_text: str,
    emotion: Optional[str] = None,
    route: Optional[str] = None,
    llm_used: Optional[str] = None,
    voice_used: Optional[str] = None,
    ttfw_ms: Optional[int] = None,
    total_audio_ms: Optional[int] = None,
    user_followup_30s: bool = False,
    route_fallback: bool = False,
    notes: str = "",
    subagent: Optional[str] = None,
    interrupted: bool = False,
    input_tokens: Optional[int] = None,
    output_tokens: Optional[int] = None,
    cost_usd: Optional[float] = None,
    context_pressure: Optional[str] = None,
    memory_auto_extracted: bool = False,
    prompt_cached_tokens: int = 0,
    browser_backend: Optional[str] = None,
    computer_use_steps: Optional[int] = None,
    computer_use_cost_usd: Optional[float] = None,
    ts_utc: Optional[str] = None,
    confab_check_state: Optional[str] = None,
    confab_pattern_matched: Optional[str] = None,
    confab_retry_models: Optional[str] = None,
    aec_layer1_active: Optional[int] = None,
    aec_layer2_aec_active: Optional[int] = None,
    aec_layer3_active: Optional[int] = None,
    output_profile: Optional[str] = None,
    apm_delay_ms_p50: Optional[int] = None,
    dtln_latency_ms_p95: Optional[float] = None,
    subagent_type: Optional[str] = None,
    subagent_ms: Optional[int] = None,
    subagent_status: Optional[str] = None,
    user_lang: str = "en",
) -> None:
    """Write one row. Any exception is swallowed so telemetry never blocks voice.

    `ts_utc=None` auto-generates current UTC via `time.strftime(...)` —
    pass an explicit ISO-8601 string only for tests/backfill. Empty
    string is a legitimate value, not a NULL sentinel; never pass
    `ts_utc=""`.

    `user_followup_30s` and `route_fallback` default to False meaning
    "not observed" — they're absence-tolerant absence flags, not
    asserted negatives. Future callers wanting to record observed
    events must pass True explicitly.

    `subagent` is the registry name (`desktop`, `planner`, `browser`, …)
    of the sub-agent that owned this turn — set when a `transfer_to_X`
    handoff fired during the turn, None otherwise.

    `interrupted` is True if the user barged in during the agent's reply,
    fired a kill-phrase, or the framework auto-interrupted this turn.

    `input_tokens` / `output_tokens` come from the LLM response usage
    field. `cost_usd` is computed via tools.token_estimation.cost_usd().
    `context_pressure` is "ok" / "warn" / "hard" from the pre-flight
    estimate at turn start.

    `browser_backend` is 'ext' / 'cdp' / None — set by the browser
    subagent's tool-factory router (2026-05-18). NULL on non-browser
    turns so a GROUP BY counts only meaningful rows.

    `computer_use_steps` is the total action count if a computer_use
    subagent handled this turn, None otherwise. `computer_use_cost_usd`
    is the sum of per-action costs (2026-05-18).

    `confab_pattern_matched` is the `_STRONG_CLAIMS` regex source string
    that tripped the pre-TTS confab gate (2026-05-24). `confab_retry_models`
    is a JSON-encoded list of model ids tried in order during the retry
    chain (e.g. `'["claude-sonnet-4-6", "claude-opus-4-7"]'`). Both NULL
    on clean turns or when the kill switch was set.
    """
    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """INSERT INTO turns
                   (ts_utc, user_text, jarvis_text, emotion, route, llm_used,
                    voice_used, ttfw_ms, total_audio_ms, user_followup_30s,
                    route_fallback, notes, subagent, interrupted,
                    input_tokens, output_tokens, cost_usd, context_pressure,
                    memory_auto_extracted, prompt_cached_tokens,
                    browser_backend,
                    computer_use_steps, computer_use_cost_usd,
                    confab_check_state,
                    confab_pattern_matched, confab_retry_models,
                    aec_layer1_active, aec_layer2_aec_active, aec_layer3_active,
                    output_profile, apm_delay_ms_p50, dtln_latency_ms_p95,
                    subagent_type, subagent_ms, subagent_status,
                    user_lang)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    ts_utc or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    user_text, jarvis_text, emotion, route, llm_used,
                    voice_used, ttfw_ms, total_audio_ms,
                    int(user_followup_30s), int(route_fallback), notes,
                    subagent, int(interrupted),
                    input_tokens, output_tokens, cost_usd, context_pressure,
                    int(memory_auto_extracted), int(prompt_cached_tokens),
                    browser_backend,
                    computer_use_steps, computer_use_cost_usd,
                    confab_check_state,
                    confab_pattern_matched, confab_retry_models,
                    aec_layer1_active, aec_layer2_aec_active, aec_layer3_active,
                    output_profile, apm_delay_ms_p50, dtln_latency_ms_p95,
                    subagent_type, subagent_ms, subagent_status,
                    user_lang,
                ),
            )
    except Exception as e:
        global _log_turn_fail_count
        _log_turn_fail_count += 1
        if _log_turn_fail_count == 1 or _log_turn_fail_count % 100 == 0:
            logger.warning(
                f"[telemetry] log_turn write failed (#{_log_turn_fail_count}) "
                f"[{type(e).__name__}]: {e} — turn not recorded. The confab "
                f"gate reads this DB; persistent failure degrades evidence checks."
            )
        return


def log_computer_use_action(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    handoff_id: str,
    step: int,
    model_used: Optional[str],
    action: str,
    params_json: Optional[str] = None,
    success: bool = True,
    screenshot_path: Optional[str] = None,
    notes: Optional[str] = None,
    pwd_check_state: Optional[str] = None,
) -> None:
    """Append one row to the `computer_use_actions` audit table.

    Failures are swallowed silently — same posture as `log_turn`. The
    computer_use loop must never crash because the audit DB is locked
    or full.
    """
    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """INSERT INTO computer_use_actions
                   (ts_utc, handoff_id, step, model_used, action,
                    params_json, success, screenshot_path, notes,
                    pwd_check_state)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    handoff_id, step, model_used, action,
                    params_json, int(success), screenshot_path, notes,
                    pwd_check_state,
                ),
            )
    except Exception:
        return


def record_browser_step(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    task: str,
    step_index: int,
    action: Optional[str] = None,
    ok: bool = True,
    detail: Optional[str] = None,
) -> None:
    """Append one row to the `browser_task_steps` audit table.

    Surfaces a single browser_use Agent step (from
    browser_use_bridge/runner.py via tools/browser.py) so a failed
    browser_task is debuggable post-mortem. Failures are swallowed
    silently — same posture as `log_turn` / `log_computer_use_action`:
    telemetry must never crash the browser tool. The table is created
    lazily here too, so a caller that writes before `init_db` ran on a
    fresh DB still works.
    """
    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS browser_task_steps (
                    id INTEGER PRIMARY KEY,
                    ts_utc TEXT NOT NULL,
                    task TEXT NOT NULL,
                    step_index INTEGER NOT NULL,
                    action TEXT,
                    ok INTEGER NOT NULL,
                    detail TEXT
                )"""
            )
            conn.execute(
                """INSERT INTO browser_task_steps
                   (ts_utc, task, step_index, action, ok, detail)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    task, int(step_index), action, int(ok), detail,
                ),
            )
    except Exception:
        return


def log_launch_attempt(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    binary: str,
    outcome: str,
) -> None:
    """Write one launch_attempts row. Outcome is `OK | MISSING | CRASHED`.

    Called from launch_app() after the verification step. Failures are
    swallowed — telemetry never blocks the user-visible reply.
    """
    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO launch_attempts (ts_utc, binary, outcome) VALUES (?, ?, ?)",
                (
                    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    binary,
                    outcome,
                ),
            )
    except Exception:
        return


def _median_int(values: list[int]) -> Optional[int]:
    """Inline median because pulling statistics for one call is overkill,
    and SQLite has no MEDIAN() aggregate."""
    nums = [v for v in values if v is not None]
    if not nums:
        return None
    nums.sort()
    n = len(nums)
    return nums[n // 2] if n % 2 else (nums[n // 2 - 1] + nums[n // 2]) // 2


def report(
    db_path: Path = DEFAULT_DB_PATH,
    *,
    days: Optional[int] = None,
    ttfw_target_ms: int = TTFW_TARGET_MS,
) -> str:
    """Human-readable telemetry summary.

    Args:
        days: If set, restrict to turns within the last N days.
        ttfw_target_ms: TTFW SLO; report says hit-rate against it.
    """
    if not Path(db_path).exists():
        return "no telemetry yet"
    out: list[str] = []
    where_sql, where_args = "", ()
    if days is not None and days > 0:
        cutoff = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ",
            time.gmtime(time.time() - days * 86400),
        )
        where_sql = " WHERE ts_utc >= ?"
        where_args = (cutoff,)

    with sqlite3.connect(db_path) as conn:
        n = conn.execute(
            f"SELECT COUNT(*) FROM turns{where_sql}", where_args
        ).fetchone()[0]
        scope = f"last {days}d" if days else "all-time"
        out.append(f"telemetry — scope={scope}, total turns={n}, ttfw_target={ttfw_target_ms}ms")
        if n == 0:
            return "\n".join(out)

        # ── TTFW target hit-rate (overall) ─────────────────────────
        hit = conn.execute(
            f"SELECT COUNT(*) FROM turns{where_sql}"
            f"{' AND' if where_sql else ' WHERE'} ttfw_ms IS NOT NULL AND ttfw_ms <= ?",
            (*where_args, ttfw_target_ms),
        ).fetchone()[0]
        measured = conn.execute(
            f"SELECT COUNT(*) FROM turns{where_sql}"
            f"{' AND' if where_sql else ' WHERE'} ttfw_ms IS NOT NULL",
            where_args,
        ).fetchone()[0]
        if measured:
            out.append(
                f"ttfw target hit-rate: {hit/measured:.0%} "
                f"({hit}/{measured} turns ≤ {ttfw_target_ms}ms)"
            )
        else:
            out.append("ttfw target hit-rate: n/a (no measured turns)")

        # ── Per-route stats (count, mean, median, max, hit-rate) ───
        out.append("by route:")
        seen_routes: set[str] = set()
        for route, count, avg_ttfw, max_ttfw in conn.execute(
            f"""SELECT route, COUNT(*) AS c,
                       CAST(AVG(ttfw_ms) AS INT),
                       MAX(ttfw_ms)
                FROM turns{where_sql}
                GROUP BY route ORDER BY c DESC""",
            where_args,
        ):
            label = route or "?"
            seen_routes.add(label)
            ttfws = [
                row[0] for row in conn.execute(
                    f"SELECT ttfw_ms FROM turns{where_sql}"
                    f"{' AND' if where_sql else ' WHERE'} route IS ? AND ttfw_ms IS NOT NULL",
                    (*where_args, route),
                )
            ]
            med = _median_int(ttfws)
            r_hit = sum(1 for t in ttfws if t <= ttfw_target_ms) if ttfws else 0
            r_rate = (r_hit / len(ttfws)) if ttfws else 0.0
            share = count / n
            out.append(
                f"  {label}: {count} turns ({share:.0%}), "
                f"avg={avg_ttfw}ms, median={med}ms, max={max_ttfw}ms, "
                f"hit-rate={r_rate:.0%}"
            )

        # ── Route-distribution health check ────────────────────────
        # Spec acceptance: "no route receives <5% of total traffic".
        # Flag both under-served (in seen_routes but below floor) and
        # missing (one of ALL_ROUTES with zero rows).
        warnings: list[str] = []
        for r in ALL_ROUTES:
            r_count = conn.execute(
                f"SELECT COUNT(*) FROM turns{where_sql}"
                f"{' AND' if where_sql else ' WHERE'} route = ?",
                (*where_args, r),
            ).fetchone()[0]
            if r_count == 0:
                warnings.append(f"route {r} has no turns")
            elif r_count / n < ROUTE_HEALTH_FLOOR:
                warnings.append(
                    f"route {r} is under-served "
                    f"({r_count}/{n} = {r_count/n:.1%}, floor {ROUTE_HEALTH_FLOOR:.0%})"
                )
        if warnings:
            out.append("route health: WARN")
            for w in warnings:
                out.append(f"  - {w}")
        else:
            out.append("route health: OK")

        # ── Emotion distribution ───────────────────────────────────
        emo_rows = list(conn.execute(
            f"""SELECT COALESCE(emotion, '?'), COUNT(*)
                FROM turns{where_sql}
                GROUP BY emotion ORDER BY 2 DESC""",
            where_args,
        ))
        if emo_rows:
            parts = ", ".join(f"{e}={c}" for e, c in emo_rows)
            out.append(f"emotion distribution: {parts}")

        # ── Subagent usage distribution ─────────────────────────
        # `subagent` is None for turns the supervisor handled directly
        # (no handoff), and a registry name (desktop/planner/browser/…)
        # when a transfer_to_X fired. Lets us see which subagents are
        # dead weight (hint to disable) and which are over-used (hint
        # to split further).
        spec_rows = list(conn.execute(
            f"""SELECT COALESCE(subagent, 'supervisor'), COUNT(*)
                FROM turns{where_sql}
                GROUP BY subagent ORDER BY 2 DESC""",
            where_args,
        ))
        if spec_rows:
            spec_pct = lambda c: f"{c}/{n} ({c/n:.0%})"
            parts = ", ".join(f"{s}={spec_pct(c)}" for s, c in spec_rows)
            out.append(f"subagent usage: {parts}")

        # ── Interruption rate (overall + per-route) ───────────────
        # Phase 10.5 — surfaces the impact of per-route + per-emotion
        # interrupt tuning. A route with a markedly higher interrupt
        # rate than its base means the overlay isn't padding enough
        # for that route's typical pace.
        intr_total = conn.execute(
            f"SELECT AVG(COALESCE(interrupted, 0)) FROM turns{where_sql}",
            where_args,
        ).fetchone()[0] or 0
        out.append(f"interruption rate (overall): {intr_total:.1%}")
        intr_rows = list(conn.execute(
            f"""SELECT COALESCE(route, '?'),
                       AVG(COALESCE(interrupted, 0)),
                       COUNT(*)
                FROM turns{where_sql}
                GROUP BY route
                HAVING COUNT(*) >= 5
                ORDER BY 2 DESC""",
            where_args,
        ))
        if intr_rows:
            out.append("interruption rate by route:")
            for r, rate, c in intr_rows:
                out.append(f"  {r}: {rate:.1%} ({c} turns)")

        # ── Emotional follow-up rate + route-fallback rate ────────
        emo_followup = conn.execute(
            f"SELECT AVG(user_followup_30s) FROM turns{where_sql}"
            f"{' AND' if where_sql else ' WHERE'} route='EMOTIONAL'",
            where_args,
        ).fetchone()[0]
        out.append(f"emotional follow-up rate: {(emo_followup or 0):.0%}")
        fb = conn.execute(
            f"SELECT AVG(route_fallback) FROM turns{where_sql}",
            where_args,
        ).fetchone()[0] or 0
        out.append(f"route-fallback rate: {fb:.1%}")

        # ── Cost + token spend (2026-05-05, ported from claude-code) ─
        # Only show when there's data — a fresh post-migration db
        # has all NULLs and shouldn't print the section.
        try:
            cost_row = conn.execute(
                f"""SELECT
                       SUM(input_tokens),
                       SUM(output_tokens),
                       SUM(cost_usd),
                       COUNT(cost_usd)
                    FROM turns{where_sql}""",
                where_args,
            ).fetchone()
        except sqlite3.OperationalError:
            cost_row = (None, None, None, 0)
        if cost_row and cost_row[3]:
            in_tok, out_tok, total_cost, n_priced = cost_row
            in_tok = in_tok or 0
            out_tok = out_tok or 0
            total_cost = total_cost or 0.0
            avg_cost = total_cost / n_priced if n_priced else 0.0
            out.append(
                f"cost: ${total_cost:.4f} total ({n_priced} priced turns, "
                f"avg ${avg_cost:.5f}/turn) — "
                f"input={in_tok:,} tok, output={out_tok:,} tok"
            )
            # Per-route + per-model cost rollup. Useful for
            # answering "where did the budget go?".
            for route_or_model, calls, cost in conn.execute(
                f"""SELECT COALESCE(llm_used, '?'),
                           COUNT(cost_usd),
                           SUM(cost_usd)
                    FROM turns{where_sql}
                    {' AND' if where_sql else ' WHERE'} cost_usd IS NOT NULL
                    GROUP BY llm_used
                    ORDER BY 3 DESC""",
                where_args,
            ):
                out.append(
                    f"  {route_or_model}: {calls} calls, "
                    f"${cost or 0.0:.4f}"
                )

        # ── Context-pressure distribution (2026-05-05) ────────────────
        # How often did we approach the 128K cap during the window?
        # WARN means context was 78%+ full; HARD means 90%+ — in
        # practice the supervisor should never see HARD because it
        # would have been compacted before the call. WARN > 5% means
        # auto-compaction is overdue.
        try:
            press_rows = list(conn.execute(
                f"""SELECT COALESCE(context_pressure, 'unmeasured'), COUNT(*)
                    FROM turns{where_sql}
                    GROUP BY context_pressure ORDER BY 2 DESC""",
                where_args,
            ))
        except sqlite3.OperationalError:
            press_rows = []
        if press_rows and any(p != "unmeasured" for p, _ in press_rows):
            parts = ", ".join(f"{p}={c}" for p, c in press_rows)
            out.append(f"context pressure: {parts}")

        # ── launch_app outcomes (Phase 10.6) ─────────────────────────
        # Only show this section when there's data — fresh dbs and
        # voice-only sessions have no launch attempts.
        launch_where = ""
        launch_args: tuple = ()
        if days is not None and days > 0:
            launch_where = " WHERE ts_utc >= ?"
            launch_args = where_args
        try:
            total_attempts = conn.execute(
                f"SELECT COUNT(*) FROM launch_attempts{launch_where}",
                launch_args,
            ).fetchone()[0]
        except sqlite3.OperationalError:
            total_attempts = 0  # fresh db without the migration yet
        if total_attempts:
            ok_n = conn.execute(
                f"SELECT COUNT(*) FROM launch_attempts{launch_where}"
                f"{' AND' if launch_where else ' WHERE'} outcome='OK'",
                launch_args,
            ).fetchone()[0]
            out.append(
                f"launch attempts: {total_attempts} ({ok_n}/{total_attempts} OK"
                f", {ok_n/total_attempts:.0%} success)"
            )
            # Per-binary breakdown — limit to top 8 by attempt count
            # so a noisy session doesn't blow up the report.
            for binary, ok, missing, crashed in conn.execute(
                f"""SELECT binary,
                           SUM(CASE outcome WHEN 'OK' THEN 1 ELSE 0 END),
                           SUM(CASE outcome WHEN 'MISSING' THEN 1 ELSE 0 END),
                           SUM(CASE outcome WHEN 'CRASHED' THEN 1 ELSE 0 END)
                    FROM launch_attempts{launch_where}
                    GROUP BY binary
                    ORDER BY COUNT(*) DESC
                    LIMIT 8""",
                launch_args,
            ):
                # Only call out problem rows in the per-binary section —
                # OK-only binaries clutter the output without adding signal.
                if (missing or 0) or (crashed or 0):
                    out.append(
                        f"  {binary}: ok={ok or 0} missing={missing or 0} crashed={crashed or 0}"
                    )
    return "\n".join(out)


def _parse_days_arg(argv: list[str]) -> Optional[int]:
    """Pull `--days N` out of argv if present, return N or None."""
    for i, a in enumerate(argv):
        if a == "--days" and i + 1 < len(argv):
            try:
                v = int(argv[i + 1])
                return v if v > 0 else None
            except ValueError:
                return None
    return None


if __name__ == "__main__":
    if "--report" in sys.argv:
        print(report(days=_parse_days_arg(sys.argv)))
    else:
        init_db()
        print(f"initialized {DEFAULT_DB_PATH}")
