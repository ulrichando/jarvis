"""SQLite turn telemetry. Non-blocking writes; failures are silent.

Every JARVIS turn writes one row. Reading is via `--report` (optionally
scoped with `--days N`).
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Optional

# Acceptance threshold for time-to-first-word, in milliseconds. Defaults
# to 1000ms (the spec target). Configurable so the CI / dogfood signal
# can tighten or loosen without code changes.
TTFW_TARGET_MS = int(os.environ.get("JARVIS_TTFW_TARGET_MS", "1000"))

# A route receiving fewer than this fraction of recent turns flags as
# "under-served" — typically a sign the classifier collapsed onto a
# single route (e.g. always TASK). Spec calls this out as an acceptance
# signal: "no route receives <5% of total traffic".
ROUTE_HEALTH_FLOOR = 0.05

# All four routes the classifier is supposed to produce. Used by the
# health check to tell us about routes that produced ZERO traffic in
# the window — those won't show up in a GROUP BY otherwise.
ALL_ROUTES = ("BANTER", "TASK", "REASONING", "EMOTIONAL")

DEFAULT_DB_PATH = Path(
    os.environ.get(
        "JARVIS_TELEMETRY_PATH",
        Path.home() / ".local" / "share" / "jarvis" / "turn_telemetry.db",
    )
).expanduser()

# Base schema — does NOT include `specialist`. That column is added
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
    notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_turns_ts_utc ON turns(ts_utc);
CREATE INDEX IF NOT EXISTS idx_turns_route  ON turns(route);

-- Phase 10.6 — launch_app outcome ledger. One row per launch attempt
-- across all sessions. Lets the report surface per-binary OK / MISSING
-- / CRASHED counts so we can spot patterns like "users keep asking for
-- 'notepad' but it isn't installed → suggest adding mousepad to the
-- specialist's app-name lookup".
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
        if "specialist" not in cols:
            try:
                conn.execute("ALTER TABLE turns ADD COLUMN specialist TEXT")
            except sqlite3.OperationalError:
                pass
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
        conn.execute("CREATE INDEX IF NOT EXISTS idx_turns_specialist ON turns(specialist)")


def log_turn(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    user_text: str,
    jarvis_text: str,
    emotion: Optional[str],
    route: Optional[str],
    llm_used: Optional[str],
    voice_used: Optional[str],
    ttfw_ms: Optional[int],
    total_audio_ms: Optional[int],
    user_followup_30s: bool,
    route_fallback: bool,
    notes: str = "",
    specialist: Optional[str] = None,
    interrupted: bool = False,
) -> None:
    """Write one row. Any exception is swallowed so telemetry never blocks voice.

    `specialist` is the registry name (`desktop`, `planner`, `browser`, …)
    of the sub-agent that owned this turn — set when a `transfer_to_X`
    handoff fired during the turn, None otherwise.

    `interrupted` is True if the user barged in during the agent's reply,
    fired a kill-phrase, or the framework auto-interrupted this turn.
    """
    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """INSERT INTO turns
                   (ts_utc, user_text, jarvis_text, emotion, route, llm_used,
                    voice_used, ttfw_ms, total_audio_ms, user_followup_30s,
                    route_fallback, notes, specialist, interrupted)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    user_text, jarvis_text, emotion, route, llm_used,
                    voice_used, ttfw_ms, total_audio_ms,
                    int(user_followup_30s), int(route_fallback), notes,
                    specialist, int(interrupted),
                ),
            )
    except Exception:
        return  # silent — see module docstring


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

        # ── Specialist usage distribution ─────────────────────────
        # `specialist` is None for turns the supervisor handled directly
        # (no handoff), and a registry name (desktop/planner/browser/…)
        # when a transfer_to_X fired. Lets us see which specialists are
        # dead weight (hint to disable) and which are over-used (hint
        # to split further).
        spec_rows = list(conn.execute(
            f"""SELECT COALESCE(specialist, 'supervisor'), COUNT(*)
                FROM turns{where_sql}
                GROUP BY specialist ORDER BY 2 DESC""",
            where_args,
        ))
        if spec_rows:
            spec_pct = lambda c: f"{c}/{n} ({c/n:.0%})"
            parts = ", ".join(f"{s}={spec_pct(c)}" for s, c in spec_rows)
            out.append(f"specialist usage: {parts}")

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
