# JARVIS Self-Evolution Gate (Honest Fitness Function) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to
> implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A read-only subsystem that computes a trustworthy, external, hard-to-game **fitness
reading** (vector + lexicographic guardrails + transparent composite) from JARVIS's existing
turn telemetry, and records readings to an append-only **evolution ledger**. Zero behavior
change. Env-gated. Trust-built via a back-test against known good/bad windows.

> **CORRECTED 2026-05-30 after empirical grounding.** The first draft built axes on columns the
> live DB never populates (`tool_call_count`/`had_tool_error`=0 always; `correction_signal`=NULL
> always; `recurring_*` empty) and an invented `confab_check_state` vocabulary — every window
> scored ~0.985. This plan uses ONLY the live-populated signals, verified against the 2876-row
> live DB: **re-ask rate**, **confab-quality** (real `confab_check_state` vocabulary), **latency**
> (`ttfw_ms` vs target 1000), **clean-action rate** (`clean_tool_called`), and **interruption**
> (low weight; empirically ambiguous so never a guardrail).

**Architecture:** Pure functions over telemetry rows (`signals.py` → `fitness.py`), a read-only
telemetry reader (`db_read.py`), an append-only store (`ledger.py`), a validation harness
(`backtest.py`), and a CLI (`bin/jarvis-evolution`). Nothing imports into the voice-agent
runtime; no import-time side effects.

**Tech stack:** Python 3.13, stdlib `sqlite3`, `dataclasses`, `argparse`, `pytest`. Use the
voice-agent venv: `src/voice-agent/.venv/bin/python`. Spec:
`docs/superpowers/specs/2026-05-30-evolution-gate-fitness-design.md`. The real
`confab_check_state` vocabulary is defined in `src/voice-agent/pipeline/turn_telemetry.py:38-70`.

---

## File Structure

- Create: `src/voice-agent/evolution/__init__.py` — package marker (1-line docstring, no imports).
- Create: `src/voice-agent/evolution/db_read.py` — read-only telemetry access → `list[dict]`.
- Create: `src/voice-agent/evolution/signals.py` — pure signal extraction → `WindowSignals`.
- Create: `src/voice-agent/evolution/fitness.py` — weights/guardrails config + `score()` + `is_fitter()`.
- Create: `src/voice-agent/evolution/ledger.py` — append-only `evolution_ledger.db`.
- Create: `src/voice-agent/evolution/backtest.py` — window scoring + comparison harness.
- Create: `bin/jarvis-evolution` — CLI (`score` / `backtest` / `ledger`).
- Create: `src/voice-agent/tests/test_evolution_ledger.py`
- Create: `src/voice-agent/tests/test_evolution_signals.py`
- Create: `src/voice-agent/tests/test_evolution_fitness.py`
- Create: `src/voice-agent/tests/test_evolution_backtest.py`

**Constitutional note (do NOT act on in this plan):** after calibration, `fitness.py` + its
weights/guardrails will be added to the auto-mod `HARD_BLOCKLIST_PATHS`. That is a *later*
increment — this plan must NOT touch `pipeline/automod/` or the blocklist.

---

### Task 1: Append-only evolution ledger

**Files:** Create `src/voice-agent/evolution/__init__.py`, `.../ledger.py`,
`tests/test_evolution_ledger.py`.

Contract:
```python
# ledger.py
from __future__ import annotations
import json, sqlite3
from pathlib import Path
from typing import Optional

DEFAULT_LEDGER_DB = Path.home() / ".local/share/jarvis/evolution_ledger.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc TEXT NOT NULL, window_start TEXT, window_end TEXT,
    n_turns INTEGER NOT NULL, per_axis_json TEXT NOT NULL, composite REAL NOT NULL,
    guardrail_json TEXT NOT NULL, passed INTEGER NOT NULL, candidate_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_readings_ts ON readings(ts_utc);
"""

def init_ledger(db_path: Path = DEFAULT_LEDGER_DB) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path)); con.executescript(_SCHEMA); con.commit(); con.close()

def append_reading(*, ts_utc: str, window_start: Optional[str], window_end: Optional[str],
                   n_turns: int, per_axis: dict, composite: float, guardrail_flags: dict,
                   passed: bool, candidate_id: Optional[str] = None,
                   db_path: Path = DEFAULT_LEDGER_DB) -> int:
    """Append one fitness reading. Returns new row id. Append-only: no update/delete API."""
    init_ledger(db_path)
    con = sqlite3.connect(str(db_path))
    cur = con.execute(
        "INSERT INTO readings(ts_utc,window_start,window_end,n_turns,per_axis_json,composite,"
        "guardrail_json,passed,candidate_id) VALUES (?,?,?,?,?,?,?,?,?)",
        (ts_utc, window_start, window_end, n_turns, json.dumps(per_axis), composite,
         json.dumps(guardrail_flags), 1 if passed else 0, candidate_id))
    con.commit(); rid = cur.lastrowid; con.close(); return rid

def read_readings(limit: int = 50, db_path: Path = DEFAULT_LEDGER_DB) -> list[dict]:
    if not Path(db_path).exists(): return []
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True); con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM readings ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    con.close()
    out = []
    for r in rows:
        d = dict(r); d["per_axis"] = json.loads(d.pop("per_axis_json"))
        d["guardrail_flags"] = json.loads(d.pop("guardrail_json"))
        d["passed"] = bool(d["passed"])          # coerce 1/0 -> bool (clean contract)
        out.append(d)
    return out
```

- [ ] **1.1 Failing tests** `test_evolution_ledger.py`:
  - `test_append_then_read_roundtrip(tmp_path)`: append → read back; assert `n_turns`, `composite`, `per_axis` dict equal, and `read_readings(...)[0]["passed"] is True` (proves bool coercion).
  - `test_append_only_no_mutation_api()`: `not hasattr(ledger,"update_reading") and not hasattr(ledger,"delete_reading")`.
  - `test_read_missing_db_returns_empty(tmp_path)`: `read_readings(db_path=tmp_path/"none.db") == []`.
  - `test_init_is_idempotent(tmp_path)`: call twice, no error.
- [ ] **1.2** `cd src/voice-agent && .venv/bin/python -m pytest tests/test_evolution_ledger.py -q` → FAIL.
- [ ] **1.3** Implement `__init__.py` (`"""JARVIS self-evolution gate (read-only fitness + ledger)."""`) + `ledger.py`.
- [ ] **1.4** Re-run → PASS.
- [ ] **1.5** Commit: `feat(evolution): append-only fitness ledger`

---

### Task 2: Read-only telemetry reader

**Files:** Create `.../db_read.py`; create `tests/test_evolution_signals.py` (shared with Task 3).

Live `turns` columns used (verified populated): `ts_utc, route, ttfw_ms, interrupted,
confab_check_state, user_text`. (The dead `tool_call_count/had_tool_error/correction_signal`
are deliberately NOT read.) **No `claude_only` param** — `turn_telemetry.db` holds only
base-mode turns; the per-route LLM variety is intentional and all of it is measured.

Contract:
```python
# db_read.py
from __future__ import annotations
import sqlite3
from pathlib import Path
from typing import Optional

DEFAULT_TELEMETRY_DB = Path.home() / ".local/share/jarvis/turn_telemetry.db"
_COLS = ["ts_utc", "route", "ttfw_ms", "interrupted", "confab_check_state", "user_text"]

def read_turns(db_path: Path = DEFAULT_TELEMETRY_DB, *, since: Optional[str] = None,
               until: Optional[str] = None, limit: Optional[int] = None) -> list[dict]:
    """Read turns read-only as list[dict]. Returns [] if the file/table is absent or has none
    of the needed columns. Missing columns come back as None."""
    if not Path(db_path).exists(): return []
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True); con.row_factory = sqlite3.Row
    tables = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "turns" not in tables: con.close(); return []
    have = {r[1] for r in con.execute("PRAGMA table_info(turns)").fetchall()}
    sel = [c for c in _COLS if c in have]
    if not sel: con.close(); return []
    where, args = [], []
    if since and "ts_utc" in have: where.append("ts_utc >= ?"); args.append(since)
    if until and "ts_utc" in have: where.append("ts_utc <= ?"); args.append(until)
    sql = f"SELECT {','.join(sel)} FROM turns"
    if where: sql += " WHERE " + " AND ".join(where)
    if "ts_utc" in have: sql += " ORDER BY ts_utc ASC"
    if limit: sql += f" LIMIT {int(limit)}"
    rows = [dict(r) for r in con.execute(sql, args).fetchall()]; con.close()
    for r in rows:
        for c in _COLS: r.setdefault(c, None)
    return rows
```

- [ ] **2.1 Failing tests** in `test_evolution_signals.py` (`_make_db(tmp_path, rows)` builds a
  minimal `turns` table with the 6 cols):
  - `test_read_turns_roundtrip`: insert 3 rows → 3 dicts, all `_COLS` keys present.
  - `test_read_turns_since_filter`: `since` excludes earlier rows.
  - `test_read_turns_tolerates_missing_columns`: table with only `ts_utc` → rows have other keys = `None`, no crash.
  - `test_read_turns_no_table_returns_empty`: a DB file with NO `turns` table → `[]` (no crash). *(regression guard for the table-less bug)*
  - `test_read_missing_db_returns_empty`.
- [ ] **2.2** Run → FAIL. **2.3** Implement. **2.4** Run → PASS.
- [ ] **2.5** Commit: `feat(evolution): read-only telemetry reader (table-less-safe)`

---

### Task 3: Pure signal extraction (live-grounded)

**Files:** Create `.../signals.py`; extend `test_evolution_signals.py`.

Contract:
```python
# signals.py
from __future__ import annotations
from dataclasses import dataclass, asdict
from statistics import median
from typing import Optional

RE_ASK_WINDOW = 3   # a near-duplicate user utterance within N turns = a re-ask (failure proxy)

# Real confab_check_state vocabulary (pipeline/turn_telemetry.py:38-70).
_RECOVERED = {"caught_t1_passed", "caught_t2_passed", "caught_t3_passed",
              "no_text_t1_passed", "no_text_t2_passed", "no_text_t3_passed"}
_FAILURE   = {"caught_filler", "no_text_filler", "retry_factory_missing",
              "retry_exception", "bypassed_killed"}
_UNCHECKED = {"unchecked", None, ""}

@dataclass
class WindowSignals:
    n_turns: int
    n_checked: int            # turns whose confab gate actually ran
    reask_rate: float         # frac of turns that are a repeat of a recent utterance
    confab_quality: float     # (clean + 0.5*recovered) / checked ; 1.0 if no checked turns
    median_ttfw_ms: float
    clean_action_rate: float  # clean_tool_called / (clean_tool_called + no_text_*) ; 1.0 if none
    interruption_rate: float
    def as_dict(self) -> dict: return asdict(self)

def _norm(s: Optional[str]) -> str:
    return "".join(ch for ch in (s or "").lower() if ch.isalnum() or ch == " ").strip()

def compute_signals(turns: list[dict]) -> WindowSignals:
    n = len(turns)
    if n == 0:
        return WindowSignals(0, 0, 0.0, 1.0, 0.0, 1.0, 0.0)
    norms = [_norm(t.get("user_text")) for t in turns]
    reasks = sum(1 for i, u in enumerate(norms)
                 if u and u in norms[max(0, i - RE_ASK_WINDOW):i])
    reask_rate = reasks / n
    states = [t.get("confab_check_state") for t in turns]
    checked = [s for s in states if s not in _UNCHECKED]
    if checked:
        clean = sum(1 for s in checked if str(s).startswith("clean"))
        recovered = sum(1 for s in checked if s in _RECOVERED)
        confab_quality = min(1.0, (clean + 0.5 * recovered) / len(checked))
    else:
        confab_quality = 1.0
    tool_clean = sum(1 for s in states if s == "clean_tool_called")
    no_text = sum(1 for s in states if str(s or "").startswith("no_text"))
    clean_action_rate = (tool_clean / (tool_clean + no_text)) if (tool_clean + no_text) else 1.0
    interruption_rate = sum(1 for t in turns if (t.get("interrupted") or 0)) / n
    ttfws = [t["ttfw_ms"] for t in turns if t.get("ttfw_ms")]
    median_ttfw_ms = float(median(ttfws)) if ttfws else 0.0
    return WindowSignals(n, len(checked), reask_rate, confab_quality, median_ttfw_ms,
                         clean_action_rate, interruption_rate)
```

- [ ] **3.1 Failing tests** (plain dict fixtures, no DB):
  - `test_empty_window`: `compute_signals([])` → `n_turns==0`, `confab_quality==1.0`, `clean_action_rate==1.0`, `reask_rate==0.0`.
  - `test_reask_detected`: same `user_text` twice within window → `reask_rate>0`; 3rd repeat → counts 2 reasks.
  - `test_confab_quality`: 2 `clean*` + 1 `caught_t1_passed` (recovered) + 1 `unchecked` → checked=3, quality=(2+0.5)/3≈0.833.
  - `test_confab_failure_drags_quality`: 1 `clean` + 1 `no_text_filler` → quality=(1+0)/2=0.5.
  - `test_clean_action_rate`: 3 `clean_tool_called` + 1 `no_text_t1_passed` → 3/4=0.75.
  - `test_median_ttfw`: `[100,200,300]`→200.0; none→0.0.
  - `test_interruption_rate`: 1 of 4 interrupted → 0.25.
- [ ] **3.2** Run → FAIL. **3.3** Implement. **3.4** Run → PASS.
- [ ] **3.5** Commit: `feat(evolution): pure signal extraction (re-ask + confab vocabulary)`

---

### Task 4: Fitness — normalization, guardrails, composite, is_fitter

**Files:** Create `.../fitness.py`, `tests/test_evolution_fitness.py`. Load-bearing module;
weights + guardrail floors are the human-owned "constitution".

Contract:
```python
# fitness.py
from __future__ import annotations
import os
from dataclasses import dataclass
from .signals import WindowSignals

# --- CONSTITUTION: weights (sum 1.0) + guardrail floors. Human-owned; later → blocklist. ---
WEIGHTS = {"reask": 0.35, "confab": 0.25, "latency": 0.20, "action": 0.15, "interruption": 0.05}
# Floors on the NORMALIZED 0..1 sub-scores (higher=better). Interruption is NOT guarded
# (empirically ambiguous — active conversations interrupt more).
GUARDRAILS = {"reask": 0.70, "confab": 0.70}

def _ttfw_target_ms() -> float:
    try: return float(os.environ.get("JARVIS_TTFW_TARGET_MS", "1000")) or 1000.0
    except (TypeError, ValueError): return 1000.0

@dataclass
class FitnessReading:
    per_axis: dict
    composite: float
    guardrail_flags: dict     # axis -> True if VIOLATED
    passed: bool
    n_turns: int = 0

def _clamp(x: float) -> float: return max(0.0, min(1.0, x))

def _normalize(sig: WindowSignals) -> dict:
    target = _ttfw_target_ms()
    lat = 1.0 if sig.median_ttfw_ms <= 0 else _clamp(1.0 - (sig.median_ttfw_ms - target) / (3 * target))
    return {
        "reask":        _clamp(1.0 - sig.reask_rate),
        "confab":       _clamp(sig.confab_quality),
        "latency":      _clamp(lat),
        "action":       _clamp(sig.clean_action_rate),
        "interruption": _clamp(1.0 - sig.interruption_rate),
    }

def score(sig: WindowSignals) -> FitnessReading:
    axis = _normalize(sig)
    composite = sum(WEIGHTS[k] * axis[k] for k in WEIGHTS)
    flags = {k: (axis[k] < floor) for k, floor in GUARDRAILS.items()}
    # An empty/no-data window is never "passing" — no evidence to promote on.
    passed = (sig.n_turns > 0) and (not any(flags.values()))
    return FitnessReading(per_axis=axis, composite=round(composite, 4),
                          guardrail_flags=flags, passed=passed, n_turns=sig.n_turns)

def is_fitter(candidate: FitnessReading, incumbent: FitnessReading, min_delta: float = 0.0) -> bool:
    """Fitter iff candidate passes all guardrails AND its composite exceeds incumbent's by
    > min_delta. Guardrail failure disqualifies regardless of composite (lexicographic veto)."""
    if not candidate.passed: return False
    return (candidate.composite - incumbent.composite) > min_delta
```

- [ ] **4.1 Failing tests** (build `WindowSignals` fixtures directly):
  - `test_weights_sum_to_one`: `abs(sum(WEIGHTS.values()) - 1.0) < 1e-9`.
  - `test_perfect_window_scores_high`: reask=0, confab=1, ttfw=0, action=1, interrupt=0, n_turns=10 → composite≈1.0, `passed True`.
  - `test_reask_guardrail_veto`: reask_rate=0.5 (axis 0.5 < 0.70) → `passed False`; and `is_fitter(that, good) is False`.
  - `test_confab_guardrail_veto`: confab_quality=0.5 → `passed False`.
  - `test_interruption_never_vetoes`: interruption_rate=1.0 but reask=0/confab=1/n_turns>0 → `passed True` (interruption is not a guardrail).
  - `test_empty_window_never_passes`: `score(compute_signals([]))` → `passed False` (n_turns==0).
  - `test_latency_uses_env_target(monkeypatch)`: set `JARVIS_TTFW_TARGET_MS=2000`; ttfw=2000 → latency axis==1.0.
  - `test_is_fitter_requires_guardrails_and_delta`: guardrail-failing-but-higher-composite candidate NOT fitter; guardrail-passing-higher-composite IS fitter.
- [ ] **4.2** Run → FAIL. **4.3** Implement. **4.4** Run → PASS.
- [ ] **4.5** Commit: `feat(evolution): fitness scoring with lexicographic guardrails`

---

### Task 5: Back-test harness

**Files:** Create `.../backtest.py`, `tests/test_evolution_backtest.py`.

Contract:
```python
# backtest.py
from __future__ import annotations
from pathlib import Path
from typing import Optional
from . import db_read, signals as _sig, fitness as _fit

def score_window(db_path: Path = db_read.DEFAULT_TELEMETRY_DB, *, since: Optional[str] = None,
                 until: Optional[str] = None) -> _fit.FitnessReading:
    return _fit.score(_sig.compute_signals(db_read.read_turns(db_path, since=since, until=until)))

def compare_windows(windows: list[tuple], db_path: Path = db_read.DEFAULT_TELEMETRY_DB) -> list[dict]:
    """windows: [(label, since, until)] → [{label, composite, passed, per_axis, n_turns}]."""
    out = []
    for label, since, until in windows:
        r = score_window(db_path, since=since, until=until)
        out.append({"label": label, "composite": r.composite, "passed": r.passed,
                    "per_axis": r.per_axis, "n_turns": r.n_turns})
    return out
```

- [ ] **5.1 Failing tests** (`_make_db` builds two synthetic windows in one `turns` table):
  - `test_bad_window_scores_below_good`: GOOD window = 10 turns, distinct `user_text`, ttfw≈900,
    `confab_check_state='clean'`, interrupted=0. BAD window = 10 turns with repeated `user_text`
    (drives reask_rate high), ttfw≈3000, a couple `no_text_filler`, interrupted mixed. Assert
    `good.composite > bad.composite` AND `good.passed and not bad.passed`. *Calibration anchor.*
  - `test_score_window_empty_db_safe(tmp_path)`: empty DB file (no `turns` table) → `score_window`
    returns a reading with `passed False`, no crash.
- [ ] **5.2** Run → FAIL. **5.3** Implement. **5.4** Run → PASS.
- [ ] **5.5** Commit: `feat(evolution): back-test harness (calibration anchor)`

---

### Task 6: CLI `bin/jarvis-evolution`

**Files:** Create `bin/jarvis-evolution` (executable). Shebang:
`#!/usr/bin/env -S /home/ulrich/Documents/Projects/jarvis/src/voice-agent/.venv/bin/python -u`
then `sys.path.insert(0, "/home/ulrich/Documents/Projects/jarvis/src/voice-agent")` so
`from evolution import ...` resolves.

- `score [--since ISO] [--until ISO] [--log]` → compute over live telemetry, print a readable
  per-axis report + composite + PASS/FAIL guardrails + n_turns. Writes to the ledger **only** if
  `--log` AND `os.environ.get("JARVIS_EVOLUTION_GATE")` is truthy. Default: print only (read-only).
- `backtest --bad <since> <until> --good <since> <until>` → `compare_windows`, print both, exit
  non-zero if NOT (good.composite > bad.composite) — the gate failing its own calibration is an error.
- `ledger [--limit N]` → print recent ledger readings.

- [ ] **6.1** Implement with `argparse` subcommands; env-gate the ledger write only.
- [ ] **6.2** `chmod +x bin/jarvis-evolution`.
- [ ] **6.3** Smoke test: `bin/jarvis-evolution score` prints a report off the real (read-only)
  telemetry without error; `bin/jarvis-evolution ledger` prints (empty). Capture output.
- [ ] **6.4** Commit: `feat(evolution): jarvis-evolution CLI (score/backtest/ledger)`

---

### Task 7: Integration + verification (the real "does it work")

- [ ] **7.1** `cd src/voice-agent && .venv/bin/python -m py_compile evolution/*.py` → clean.
- [ ] **7.2** `.venv/bin/python -m pytest tests/test_evolution_*.py -q` → all pass.
- [ ] **7.3** No regression: `.venv/bin/python -c "import jarvis_agent"` still imports clean
  (evolution package has NO import-time side effects and is NOT imported by the runtime).
- [ ] **7.4 Calibration on REAL data (the acceptance gate):**
  ```
  bin/jarvis-evolution backtest \
    --bad  2026-05-30T05:22:00Z 2026-05-30T05:53:00Z \
    --good 2026-05-29T00:00:00Z 2026-05-29T23:59:59Z
  ```
  Expected (from the 2026-05-30 probe: BAD ttfw≈2838 + ~5 repeated utterances vs GOOD ttfw≈1728,
  no re-asks): **good.composite > bad.composite** and the command exits 0. Record both composites
  + per-axis. If bad ≥ good, the fitness function is mis-calibrated → STOP and fix before claiming done.
- [ ] **7.5** Final summary commit if anything was tidied.

**Acceptance:** all `test_evolution_*` pass; CLI runs read-only against live telemetry; the
back-test shows the known-bad window scoring **below** the known-good window on REAL data, driven
by re-ask + latency (not by a constant); nothing in the voice-agent runtime imports or is changed.
