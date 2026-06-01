# Evolution Calibration Soak — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a daily, read-only, gated soak that logs one honest fitness reading per day into the existing `evolution_ledger.db`, plus a `trend` CLI view — so the gate can be calibrated against felt experience before anything depends on it.

**Architecture:** Pure functions in a new `evolution/soak.py` (previous-local-day window, soak orchestration, trend formatting) + a read-only `ledger.reading_exists` dedup helper + two thin CLI subcommands (`soak`, `trend`) + a hardened `oneshot` systemd `--user` timer that runs `jarvis-evolution soak` daily with the gate env scoped to that unit only. Nothing imports into the voice-agent runtime; the gate stays globally OFF.

**Tech Stack:** Python 3.13, stdlib `sqlite3`/`datetime`/`argparse`, `pytest`, systemd `--user` units. Use the voice-agent venv: `src/voice-agent/.venv/bin/python`. Spec: `docs/superpowers/specs/2026-05-30-evolution-calibration-soak-design.md`.

---

## File Structure

- **Modify** `src/voice-agent/evolution/ledger.py` — add read-only `reading_exists(...)` (append-only preserved).
- **Create** `src/voice-agent/evolution/soak.py` — `previous_local_day_window_utc`, `run_soak`, `format_trend_table` (pure / thin-composition).
- **Modify** `bin/jarvis-evolution` — add `soak` + `trend` subcommands (thin wrappers over `soak.py`).
- **Create** `setup/systemd/jarvis-evolution-soak.timer` — daily 02:30 trigger.
- **Create** `setup/systemd/jarvis-evolution-soak.service` — hardened oneshot, gate env scoped here only.
- **Modify** `install.sh` — register the two units in the timer-install + enable-now loops.
- **Modify** `src/voice-agent/tests/test_evolution_ledger.py` — `reading_exists` tests.
- **Create** `src/voice-agent/tests/test_evolution_soak.py` — window / soak-orchestration / trend-format tests.

**Out of scope (do NOT touch):** voice-agent runtime, `pipeline/automod/` + `HARD_BLOCKLIST_PATHS` (the `fitness.py` pin is a later increment), `turn_telemetry.db` schema, and the uncommitted `dispatch_agent`/`background_tasks` WIP in the working tree (stage only the files listed per task).

**Conventions verified from the live repo:**
- Telemetry `ts_utc` format is exactly `YYYY-MM-DDTHH:MM:SSZ`; lexicographic `>=`/`<=` bounds work.
- `ledger.append_reading(*, ts_utc, window_start, window_end, n_turns, per_axis, composite, guardrail_flags, passed, candidate_id=None, db_path=...)` and `ledger.read_readings(limit, db_path)` already exist; `read_readings` returns dicts with keys `id, ts_utc, window_start, window_end, n_turns, per_axis (dict), composite, guardrail_flags (dict), passed (bool), candidate_id`, newest-first.
- `backtest.score_window(db_path=..., *, since=None, until=None) -> FitnessReading` with `.composite/.passed/.per_axis/.guardrail_flags/.n_turns`. `per_axis` keys = `reask, confab, latency, action, interruption`.
- The CLI already gates writes on `bool(os.environ.get("JARVIS_EVOLUTION_GATE"))`.

---

### Task 1: Ledger dedup helper (`reading_exists`)

**Files:**
- Modify: `src/voice-agent/evolution/ledger.py`
- Test: `src/voice-agent/tests/test_evolution_ledger.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/test_evolution_ledger.py`:

```python
def test_reading_exists_false_for_unlogged_window(tmp_path):
    from evolution import ledger
    db = tmp_path / "led.db"
    ledger.init_ledger(db)
    assert ledger.reading_exists(
        window_start="2026-05-29T04:00:00Z",
        window_end="2026-05-30T03:59:59Z", db_path=db) is False


def test_reading_exists_true_after_append(tmp_path):
    from evolution import ledger
    db = tmp_path / "led.db"
    ledger.append_reading(
        ts_utc="2026-05-30T06:30:00Z",
        window_start="2026-05-29T04:00:00Z", window_end="2026-05-30T03:59:59Z",
        n_turns=10, per_axis={"reask": 1.0}, composite=0.9,
        guardrail_flags={"reask": False}, passed=True, db_path=db)
    assert ledger.reading_exists(
        window_start="2026-05-29T04:00:00Z",
        window_end="2026-05-30T03:59:59Z", db_path=db) is True
    # a different window is still absent
    assert ledger.reading_exists(
        window_start="2026-05-28T04:00:00Z",
        window_end="2026-05-29T03:59:59Z", db_path=db) is False


def test_reading_exists_missing_db_is_false(tmp_path):
    from evolution import ledger
    assert ledger.reading_exists(
        window_start="x", window_end="y", db_path=tmp_path / "none.db") is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_evolution_ledger.py -q -k reading_exists`
Expected: FAIL with `AttributeError: module 'evolution.ledger' has no attribute 'reading_exists'`.

- [ ] **Step 3: Implement `reading_exists`** — append to `src/voice-agent/evolution/ledger.py` (after `read_readings`):

```python
def reading_exists(*, window_start: Optional[str], window_end: Optional[str],
                   db_path: Path = DEFAULT_LEDGER_DB) -> bool:
    """Read-only: True if a reading for exactly this (window_start, window_end) is
    already logged. Used by the soak to avoid double-logging on Persistent catch-up.
    Append-only is preserved — this is a pure read (`SELECT 1`)."""
    if not Path(db_path).exists():
        return False
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = con.execute(
            "SELECT 1 FROM readings WHERE window_start IS ? AND window_end IS ? LIMIT 1",
            (window_start, window_end)).fetchone()
    except sqlite3.OperationalError:        # table absent → treat as not-logged
        row = None
    finally:
        con.close()
    return row is not None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_evolution_ledger.py -q`
Expected: PASS (the new 3 + all pre-existing ledger tests, including `test_append_only_no_mutation_api`).

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/evolution/ledger.py src/voice-agent/tests/test_evolution_ledger.py
git commit -m "feat(evolution): read-only reading_exists dedup helper (append-only preserved)"
```

---

### Task 2: Previous-local-day window function

**Files:**
- Create: `src/voice-agent/evolution/soak.py`
- Test: `src/voice-agent/tests/test_evolution_soak.py`

- [ ] **Step 1: Write the failing test** — create `tests/test_evolution_soak.py`:

```python
from datetime import datetime, timedelta, timezone


def test_previous_local_day_window_utc_edt():
    from evolution import soak
    # noon EDT (UTC-4) on 2026-05-30
    now = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone(timedelta(hours=-4)))
    since, until = soak.previous_local_day_window_utc(now)
    assert since == "2026-05-29T04:00:00Z"     # local 2026-05-29 00:00 EDT
    assert until == "2026-05-30T03:59:59Z"     # local 2026-05-29 23:59:59 EDT


def test_previous_local_day_window_utc_format_is_z():
    from evolution import soak
    now = datetime(2026, 1, 15, 9, 30, 0, tzinfo=timezone(timedelta(hours=-5)))  # EST
    since, until = soak.previous_local_day_window_utc(now)
    assert since.endswith("Z") and until.endswith("Z")
    assert since == "2026-01-14T05:00:00Z" and until == "2026-01-15T04:59:59Z"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_evolution_soak.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'evolution.soak'`.

- [ ] **Step 3: Implement the window function** — create `src/voice-agent/evolution/soak.py`:

```python
"""Evolution calibration soak — daily fitness logging + trend review.

Pure window math + thin composition over the read-only reader, fitness, and the
append-only ledger. No import-time side effects; not imported by the voice-agent
runtime. The gate write is env-gated identically to `score --log`.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone


def previous_local_day_window_utc(now: datetime) -> tuple[str, str]:
    """Bounds of the *previous local calendar day* as UTC 'YYYY-MM-DDTHH:MM:SSZ'
    strings, inclusive. `now` MUST be timezone-aware; the day is computed in `now`'s
    own tz (so the result is deterministic and machine-independent), then converted
    to UTC to match telemetry's `ts_utc`."""
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    y_start = today_start - timedelta(days=1)
    y_end = today_start - timedelta(seconds=1)
    def _z(d: datetime) -> str:
        return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return _z(y_start), _z(y_end)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_evolution_soak.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/evolution/soak.py src/voice-agent/tests/test_evolution_soak.py
git commit -m "feat(evolution): previous-local-day window function (felt-experience aligned)"
```

---

### Task 3: Soak orchestration (`run_soak`)

**Files:**
- Modify: `src/voice-agent/evolution/soak.py`
- Test: `src/voice-agent/tests/test_evolution_soak.py`

`run_soak` is the testable core: compute window → dedup → score → (gated) append. Keeping it in the package (not the CLI) makes the dedup + gate logic unit-testable without argparse/env. Tests point `telemetry_db` at a nonexistent path so `score_window` returns an empty-window reading (`n_turns=0`, `passed=False`) — fine; the test only cares about *whether* a row is written.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_evolution_soak.py`:

```python
def _fixed_now():
    return datetime(2026, 5, 30, 2, 30, 0, tzinfo=timezone(timedelta(hours=-4)))


def test_run_soak_logs_then_dedups(tmp_path):
    from evolution import soak, ledger
    led = tmp_path / "led.db"
    tel = tmp_path / "none.db"          # absent telemetry → empty-window reading
    r1 = soak.run_soak(now=_fixed_now(), telemetry_db=tel, ledger_db=led, gate_on=True)
    assert r1["action"] == "logged" and r1["reading_id"] is not None
    r2 = soak.run_soak(now=_fixed_now(), telemetry_db=tel, ledger_db=led, gate_on=True)
    assert r2["action"] == "already_logged"
    assert len(ledger.read_readings(db_path=led)) == 1     # exactly one row


def test_run_soak_writes_only_under_gate(tmp_path):
    from evolution import soak, ledger
    led = tmp_path / "led.db"
    tel = tmp_path / "none.db"
    r_off = soak.run_soak(now=_fixed_now(), telemetry_db=tel, ledger_db=led, gate_on=False)
    assert r_off["action"] == "no_gate" and r_off["reading_id"] is None
    assert ledger.read_readings(db_path=led) == []         # nothing written
    r_on = soak.run_soak(now=_fixed_now(), telemetry_db=tel, ledger_db=led, gate_on=True)
    assert r_on["action"] == "logged"
    assert len(ledger.read_readings(db_path=led)) == 1


def test_run_soak_window_matches_previous_local_day(tmp_path):
    from evolution import soak
    r = soak.run_soak(now=_fixed_now(), telemetry_db=tmp_path / "none.db",
                      ledger_db=tmp_path / "led.db", gate_on=False)
    assert r["since"] == "2026-05-29T04:00:00Z"
    assert r["until"] == "2026-05-30T03:59:59Z"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_evolution_soak.py -q -k run_soak`
Expected: FAIL with `AttributeError: module 'evolution.soak' has no attribute 'run_soak'`.

- [ ] **Step 3: Implement `run_soak`** — in `src/voice-agent/evolution/soak.py`, add the import line below the existing `from datetime import ...` and append the function:

```python
from . import db_read, ledger as _ledger, backtest as _backtest
```

```python
def run_soak(*, now: datetime, telemetry_db=db_read.DEFAULT_TELEMETRY_DB,
             ledger_db=_ledger.DEFAULT_LEDGER_DB, gate_on: bool) -> dict:
    """Score the previous local day and (if gate_on) append one ledger reading.
    Idempotent per window via reading_exists. Returns a result dict describing what
    happened: action ∈ {already_logged, no_gate, logged}."""
    since, until = previous_local_day_window_utc(now)
    base = {"since": since, "until": until, "reading_id": None,
            "composite": None, "passed": None, "n_turns": None}
    if _ledger.reading_exists(window_start=since, window_end=until, db_path=ledger_db):
        return {**base, "action": "already_logged"}
    reading = _backtest.score_window(telemetry_db, since=since, until=until)
    base.update(composite=reading.composite, passed=reading.passed, n_turns=reading.n_turns)
    if not gate_on:
        return {**base, "action": "no_gate"}
    ts = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rid = _ledger.append_reading(
        ts_utc=ts, window_start=since, window_end=until, n_turns=reading.n_turns,
        per_axis=reading.per_axis, composite=reading.composite,
        guardrail_flags=reading.guardrail_flags, passed=reading.passed, db_path=ledger_db)
    return {**base, "action": "logged", "reading_id": rid}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_evolution_soak.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/evolution/soak.py src/voice-agent/tests/test_evolution_soak.py
git commit -m "feat(evolution): run_soak — dedup + gate-scoped daily ledger write"
```

---

### Task 4: Trend table formatter (`format_trend_table`)

**Files:**
- Modify: `src/voice-agent/evolution/soak.py`
- Test: `src/voice-agent/tests/test_evolution_soak.py`

- [ ] **Step 1: Write the failing test** — append to `tests/test_evolution_soak.py`:

```python
def test_format_trend_table_contains_axes_and_date():
    from evolution import soak
    rows = [{
        "window_start": "2026-05-29T04:00:00Z", "window_end": "2026-05-30T03:59:59Z",
        "n_turns": 177, "composite": 0.8523,
        "per_axis": {"reask": 0.949, "confab": 1.0, "latency": 0.374,
                     "action": 1.0, "interruption": 0.904},
        "passed": True,
    }]
    out = soak.format_trend_table(rows)
    assert "DATE" in out and "reask" in out and "confab" in out
    assert "2026-05-29" in out          # date label from window_start[:10]
    assert "177" in out and "PASS" in out


def test_format_trend_table_handles_missing_window_and_axes():
    from evolution import soak
    rows = [{"window_start": None, "window_end": None, "n_turns": 0,
             "composite": 0.0, "per_axis": {}, "passed": False}]
    out = soak.format_trend_table(rows)
    assert "(all)" in out and "FAIL" in out      # no crash on None/empty
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_evolution_soak.py -q -k trend`
Expected: FAIL with `AttributeError: module 'evolution.soak' has no attribute 'format_trend_table'`.

- [ ] **Step 3: Implement `format_trend_table`** — append to `src/voice-agent/evolution/soak.py`:

```python
def format_trend_table(readings: list[dict]) -> str:
    """Render ledger readings (newest-first, as read_readings returns) as an aligned
    per-axis table. Pure: takes dicts, returns a string. DATE = window_start[:10]
    (UTC date == local date for west-of-UTC offsets, e.g. this EDT/EST box)."""
    header = (f"{'DATE':<11} {'n':>4} {'comp':>7}  "
              f"{'reask':>6} {'confab':>6} {'lat':>6} {'action':>6} {'intr':>6}  RESULT")
    lines = [header]
    for r in readings:
        ws = r.get("window_start")
        date = ws[:10] if ws else "(all)"
        ax = r.get("per_axis") or {}

        def _fmt(key: str) -> str:
            v = ax.get(key)
            return f"{v:.3f}" if isinstance(v, (int, float)) else "-"

        lines.append(
            f"{date:<11} {r.get('n_turns', 0):>4} {r.get('composite', 0.0):>7.4f}  "
            f"{_fmt('reask'):>6} {_fmt('confab'):>6} {_fmt('latency'):>6} "
            f"{_fmt('action'):>6} {_fmt('interruption'):>6}  "
            f"{'PASS' if r.get('passed') else 'FAIL'}")
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_evolution_soak.py -q`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/evolution/soak.py src/voice-agent/tests/test_evolution_soak.py
git commit -m "feat(evolution): trend table formatter for ledger review"
```

---

### Task 5: CLI `soak` + `trend` subcommands

**Files:**
- Modify: `bin/jarvis-evolution`

These are thin wrappers — logic lives in `soak.py` (already tested). Verified by smoke in Task 7.

- [ ] **Step 1: Add the `soak` import** — in `bin/jarvis-evolution`, after line 27 (`from evolution import ledger as _led`), add:

```python
from evolution import soak as _soak      # noqa: E402
```

- [ ] **Step 2: Add the two command handlers** — insert after `_cmd_ledger` (before `def main`):

```python
def _cmd_soak(args: argparse.Namespace) -> int:
    now = _dt.datetime.now().astimezone()          # local, tz-aware
    gate_on = bool(os.environ.get("JARVIS_EVOLUTION_GATE"))
    res = _soak.run_soak(now=now, gate_on=gate_on)
    print(f"window:    {res['since']} .. {res['until']}")
    if res["action"] == "already_logged":
        print("[soak] already logged this window; skipping (idempotent).")
        return 0
    print(f"composite: {res['composite']}  passed: {res['passed']}  n_turns: {res['n_turns']}")
    if res["action"] == "no_gate":
        print("[soak] JARVIS_EVOLUTION_GATE not set; computed but not written.")
    else:  # logged
        print(f"[ledger] appended reading id={res['reading_id']}")
    return 0


def _cmd_trend(args: argparse.Namespace) -> int:
    rows = _led.read_readings(limit=args.limit)
    if not rows:
        print("(ledger empty — no readings yet; run the soak or wait for the timer)")
        return 0
    print(_soak.format_trend_table(rows))
    return 0
```

- [ ] **Step 3: Register the subparsers** — in `main()`, after the `ledger` subparser block (after `lp.set_defaults(func=_cmd_ledger)`), add:

```python
    kp = sub.add_parser("soak", help="score the previous local day + log it (gated)")
    kp.set_defaults(func=_cmd_soak)

    tp = sub.add_parser("trend", help="print a per-axis trend table of ledger readings")
    tp.add_argument("--limit", type=int, default=30)
    tp.set_defaults(func=_cmd_trend)
```

- [ ] **Step 4: Smoke-check the CLI parses + runs read-only**

Run: `cd /home/ulrich/Documents/Projects/jarvis && bin/jarvis-evolution soak`
Expected: prints a `window:` line + `composite/passed/n_turns` + `[soak] JARVIS_EVOLUTION_GATE not set; computed but not written.` (no env set → nothing written). Exit 0.
Run: `bin/jarvis-evolution trend`
Expected: `(ledger empty — no readings yet; ...)` (ledger still empty). Exit 0.

- [ ] **Step 5: Commit**

```bash
git add bin/jarvis-evolution
git commit -m "feat(evolution): jarvis-evolution soak + trend subcommands"
```

---

### Task 6: systemd units + install.sh wiring

**Files:**
- Create: `setup/systemd/jarvis-evolution-soak.timer`
- Create: `setup/systemd/jarvis-evolution-soak.service`
- Modify: `install.sh`

- [ ] **Step 1: Create the timer** — `setup/systemd/jarvis-evolution-soak.timer`:

```ini
[Unit]
Description=JARVIS — daily evolution-gate calibration soak (read-only fitness reading)
Documentation=file:%h/Documents/Projects/jarvis/docs/superpowers/specs/2026-05-30-evolution-calibration-soak-design.md

[Timer]
# Daily at 02:30 local (offset from log-rotate's 02:00). Persistent catches up
# after the laptop was off; the soak dedups per-window so a catch-up run never
# double-logs the same day.
OnCalendar=*-*-* 02:30:00
Persistent=true
RandomizedDelaySec=600s
Unit=jarvis-evolution-soak.service

[Install]
WantedBy=timers.target
```

- [ ] **Step 2: Create the service** — `setup/systemd/jarvis-evolution-soak.service` (mirrors the verified-working `jarvis-curator.service` profile; tightened to `AF_UNIX` since the soak needs no network):

```ini
[Unit]
Description=JARVIS — evolution-gate calibration soak (scores previous day → evolution_ledger.db)
Documentation=file:%h/Documents/Projects/jarvis/bin/jarvis-evolution
After=network.target

[Service]
Type=oneshot
# Pre-create the data dir — ProtectSystem=strict refuses to bind-mount a
# non-existent ReadWritePaths (status=226/NAMESPACE).
ExecStartPre=/bin/mkdir -p %h/.local/share/jarvis
# The fitness gate is flipped ON *only here*. Every other process keeps it OFF.
Environment=JARVIS_EVOLUTION_GATE=1
Environment=JARVIS_TTFW_TARGET_MS=1000
ExecStart=%h/Documents/Projects/jarvis/bin/jarvis-evolution soak

# Reads turn_telemetry.db (opened mode=ro at the app layer) and writes only
# evolution_ledger.db under ~/.local/share/jarvis/. No network (pure local sqlite).
NoNewPrivileges=yes
PrivateTmp=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectKernelLogs=yes
ProtectControlGroups=yes
ProtectClock=yes
ProtectHostname=yes
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=%h/.local/share/jarvis
RestrictAddressFamilies=AF_UNIX
LockPersonality=yes
RestrictSUIDSGID=yes
RestrictNamespaces=yes
CapabilityBoundingSet=
AmbientCapabilities=
SystemCallFilter=@system-service
SystemCallFilter=~@privileged ~@resources ~@reboot ~@module ~@swap ~@debug ~@raw-io
SystemCallErrorNumber=EPERM

[Install]
WantedBy=default.target
```

- [ ] **Step 3: Register in the install.sh timer-install loop** — change the `for src in \` list (currently ending `jarvis-retention-prune.service jarvis-retention-prune.timer; do`) to add the soak units:

```bash
  for src in \
      jarvis-backup-local.service jarvis-backup-local.timer \
      jarvis-log-rotate.service jarvis-log-rotate.timer \
      jarvis-retention-prune.service jarvis-retention-prune.timer \
      jarvis-evolution-soak.service jarvis-evolution-soak.timer; do
```

- [ ] **Step 4: Register in the install.sh enable-now loop** — change the `for unit in jarvis-backup-local.timer jarvis-log-rotate.timer jarvis-retention-prune.timer; do` line to:

```bash
  for unit in jarvis-backup-local.timer jarvis-log-rotate.timer jarvis-retention-prune.timer jarvis-evolution-soak.timer; do
```

- [ ] **Step 5: Validate unit syntax** (path-substitute into the user dir exactly as install.sh does, then verify):

```bash
cd /home/ulrich/Documents/Projects/jarvis
for u in jarvis-evolution-soak.service jarvis-evolution-soak.timer; do
  sed -e "s|%h/Documents/Projects/jarvis|$PWD|g" "setup/systemd/$u" > "$HOME/.config/systemd/user/$u"
done
systemctl --user daemon-reload
systemd-analyze --user verify "$HOME/.config/systemd/user/jarvis-evolution-soak.service" 2>&1 | grep -v 'is not known, ignoring' || true
```
Expected: no fatal errors (the `~@module/~@swap/...` "not known, ignoring" lines are benign, same as curator).

- [ ] **Step 6: Commit**

```bash
git add setup/systemd/jarvis-evolution-soak.timer setup/systemd/jarvis-evolution-soak.service install.sh
git commit -m "feat(evolution): daily soak systemd timer + install wiring (gate scoped to unit)"
```

---

### Task 7: Integration + verification (the real "does it work")

**Files:** none (verification only).

- [ ] **Step 1: All evolution tests pass**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_evolution_*.py -q`
Expected: PASS — the prior 26 + 3 ledger + 7 soak = **36** tests.

- [ ] **Step 2: Package compiles, no import-time side effects**

Run: `cd src/voice-agent && .venv/bin/python -m py_compile evolution/*.py && .venv/bin/python -c "import evolution.soak; print('ok')"`
Expected: `ok` (no error, no stray output).

- [ ] **Step 3: Soak is read-only without the gate**

Run: `cd /home/ulrich/Documents/Projects/jarvis && bin/jarvis-evolution soak && bin/jarvis-evolution trend`
Expected: `soak` prints the window + `not written`; `trend` prints `(ledger empty …)`. Confirm: `ls ~/.local/share/jarvis/evolution_ledger.db` → still absent (or unchanged).

- [ ] **Step 4: Gated soak writes one real reading + is idempotent**

```bash
cd /home/ulrich/Documents/Projects/jarvis
JARVIS_EVOLUTION_GATE=1 bin/jarvis-evolution soak     # → [ledger] appended reading id=1
JARVIS_EVOLUTION_GATE=1 bin/jarvis-evolution soak     # → [soak] already logged this window; skipping
bin/jarvis-evolution trend                            # → table with yesterday's local date
bin/jarvis-evolution ledger                           # → exactly one reading row
```
Expected: first run appends `id=1`; second run is a no-op (`already_logged`); `trend` shows one dated row with the 5 axes; `ledger` shows one row. (This seeds the real ledger with the first calibration reading.)

- [ ] **Step 5: No runtime regression**

Run: `cd src/voice-agent && .venv/bin/python -c "import jarvis_agent; print('import ok')"`
Expected: `import ok` (the evolution package is not imported by the runtime and has no import-time side effects).

- [ ] **Step 6: Enable the timer (reversible)**

```bash
systemctl --user daemon-reload
systemctl --user enable --now jarvis-evolution-soak.timer
systemctl --user list-timers 'jarvis-evolution-soak*' --all
```
Expected: the timer is listed with a NEXT fire at ~02:30 tomorrow. (Revert any time with `systemctl --user disable --now jarvis-evolution-soak.timer`.)

- [ ] **Step 7: Final end-of-task summary** (per `.claude/rules/regression-prevention.md §7`). Confirm the OUT list is untouched: `git status --short` shows the `dispatch_agent`/`background_tasks` WIP unchanged and unstaged by this work.

**Acceptance:** 36 evolution tests pass; `import jarvis_agent` clean; the soak writes exactly one reading per local day under the gate and is idempotent; `trend` renders a per-axis table; the timer is enabled with a next fire; the gate remains OFF everywhere except the soak unit; the runtime and the separate WIP are untouched.
