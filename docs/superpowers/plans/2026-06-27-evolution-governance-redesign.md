# Evolution governance redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Replace the evolution loop's per-day build *count* cap with a cost-budget + signal/idle/cooldown gate, move the build cadence to every-4-hours + nightly, and fix the dashboard queue-depth count.

**Architecture:** A new `cost_ledger.py` records each build's `total_cost_usd` (captured by running the build agent with `--output-format json`) into a per-day ledger. `throttle.admit_intent()` is rewritten to gate on idle (no recent voice turn) + budget (ledger < daily $) + cooldown, with the path-blocklist unchanged and the count cap demoted to an optional backstop. The 30-min build timer becomes 4-hourly; nightly introspect/soak and the 20s watchdog are untouched. The dashboard badge is pointed at the real queue count.

**Tech Stack:** Python 3.13 (voice-agent venv), bash (build wrapper + systemd), Next.js/TS (web dashboard), SQLite (turn_telemetry idle check).

**Spec:** `docs/superpowers/specs/2026-06-27-evolution-governance-redesign-design.md`

**Safety:** `pipeline/automod/**` is on the auto-mod HARD_BLOCKLIST — every change here is human-edited. The loop is currently **paused** (`~/.jarvis/auto-mods/.evolution-paused`); it stays paused until Task 7. Run the hermetic test runner, not bare pytest: `cd src/voice-agent && ../../bin/jarvis-automod-pytest tests/ -q` (or `.venv/bin/python -m pytest` for non-automod tests).

---

### Task 1: Cost ledger module

**Files:**
- Create: `src/voice-agent/pipeline/automod/cost_ledger.py`
- Test: `src/voice-agent/tests/test_automod_cost_ledger.py`
- Reference pattern: `src/voice-agent/pipeline/automod/throttle.py` (date-rollover + atomic write + `_state.py` path helper)

- [ ] **Step 1: Add the ledger path helper to `_state.py`**
Mirror `throttle_state_path()`. Add:
```python
def cost_ledger_path() -> Path:
    return _automods_dir() / "cost-ledger.json"
```
(Use whatever the existing `throttle_state_path` uses for the dir — reuse `_automods_dir()`/equivalent; do not hardcode.)

- [ ] **Step 2: Write the failing test**
```python
# tests/test_automod_cost_ledger.py
import importlib, json, time
from pathlib import Path
import pipeline.automod.cost_ledger as cl

def test_record_and_sum_today(tmp_path, monkeypatch):
    p = tmp_path / "cost-ledger.json"
    monkeypatch.setattr(cl, "cost_ledger_path", lambda: p)
    assert cl.spent_today() == 0.0
    cl.record("b1", 1.25)
    cl.record("b2", 0.75)
    assert round(cl.spent_today(), 2) == 2.00

def test_rollover_resets(tmp_path, monkeypatch):
    p = tmp_path / "cost-ledger.json"
    p.write_text(json.dumps({"date": "2000-01-01", "entries": [{"id":"x","cost_usd":9.0}]}))
    monkeypatch.setattr(cl, "cost_ledger_path", lambda: p)
    assert cl.spent_today() == 0.0   # stale day ignored

def test_daily_usd_env(monkeypatch):
    monkeypatch.setenv("JARVIS_EVOLUTION_DAILY_USD", "12.5")
    assert cl.daily_usd() == 12.5
    monkeypatch.delenv("JARVIS_EVOLUTION_DAILY_USD", raising=False)
    assert cl.daily_usd() == 6.0
```

- [ ] **Step 3: Run it — expect import failure**
`cd src/voice-agent && .venv/bin/python -m pytest tests/test_automod_cost_ledger.py -q` → FAIL (module missing).

- [ ] **Step 4: Implement `cost_ledger.py`**
```python
"""Per-day cost ledger for the evolution loop — the real spend brake.

Records each build's total_cost_usd; spent_today() sums the current UTC day
(date rollover resets to 0, mirroring throttle.py). Lock-free atomic write via
os.replace. JARVIS_EVOLUTION_DAILY_USD (default 6.0) is the daily ceiling the
gate checks against.
"""
from __future__ import annotations
import json, os, time
from pipeline.automod._state import cost_ledger_path

DEFAULT_DAILY_USD = 6.0

def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())

def daily_usd() -> float:
    try:
        return float(os.environ.get("JARVIS_EVOLUTION_DAILY_USD", str(DEFAULT_DAILY_USD)))
    except (TypeError, ValueError):
        return DEFAULT_DAILY_USD

def _read() -> dict:
    p = cost_ledger_path()
    if not p.exists():
        return {"date": _today(), "entries": []}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"date": _today(), "entries": []}
    if d.get("date") != _today():
        return {"date": _today(), "entries": []}
    return d

def spent_today() -> float:
    return round(sum(float(e.get("cost_usd", 0) or 0) for e in _read().get("entries", [])), 6)

def record(build_id: str, cost_usd: float) -> None:
    d = _read()
    d["entries"].append({"id": build_id, "cost_usd": float(cost_usd or 0), "ts": _today()})
    p = cost_ledger_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d), encoding="utf-8")
    os.replace(tmp, p)
```

- [ ] **Step 5: Run tests — expect PASS.** Same command as Step 3.

- [ ] **Step 6: Commit**
```bash
git add src/voice-agent/pipeline/automod/cost_ledger.py src/voice-agent/pipeline/automod/_state.py src/voice-agent/tests/test_automod_cost_ledger.py
git commit -m "feat(evolution): per-day cost ledger (the spend brake)"
```

---

### Task 2: Capture build cost

**Files:**
- Modify: `bin/jarvis-automod-impl:124` (the `bin/jarvis -p` invocation)
- Modify: `src/voice-agent/pipeline/automod/finalize.py` (record cost after build)

- [ ] **Step 1: Verify the cost field name**
Run once: `bin/jarvis -p "reply with the single word ok" --output-format json 2>/dev/null | python3 -c 'import sys,json; print([k for k in json.load(sys.stdin)])'`
Confirm a `total_cost_usd` key (Claude-Code shape). If the key differs (e.g. `cost_usd`), use that name in Step 3.

- [ ] **Step 2: Capture the result JSON in the wrapper**
In `bin/jarvis-automod-impl`, change line 124 from:
```bash
timeout "${JARVIS_AUTOMOD_BUILD_TIMEOUT_S:-1500}" "$TOOLING_ROOT/bin/jarvis" -p "$PROMPT" || true
```
to (write the agent's JSON result to a sidecar file; keep stderr in the log):
```bash
timeout "${JARVIS_AUTOMOD_BUILD_TIMEOUT_S:-1500}" "$TOOLING_ROOT/bin/jarvis" -p "$PROMPT" \
    --output-format json > "$JARVIS_HOME_DIR/auto-mods/$ID.result.json" 2>>"$LOG" || true
```
Do NOT touch the HARD RULES prompt or any other line — only this invocation.

- [ ] **Step 3: Record cost in `finalize.py`**
Where finalize writes the artifact JSON (status pending/failed), add a best-effort cost record. Near the top imports add `from pipeline.automod import cost_ledger`. After the artifact status is decided, add:
```python
# Record build spend to the daily ledger (best-effort; a failed build still spent tokens).
try:
    import json as _json
    _rp = Path.home() / ".jarvis" / "auto-mods" / f"{automod_id}.result.json"
    if _rp.exists():
        _cost = float(_json.loads(_rp.read_text()).get("total_cost_usd", 0) or 0)
        if _cost > 0:
            cost_ledger.record(automod_id, _cost)
except Exception:
    pass
```
(Match the variable name finalize uses for the id — likely `automod_id`/`id`; verify in the file.)

- [ ] **Step 4: Verify syntax + wrapper sanity**
`bash -n bin/jarvis-automod-impl && echo ok`
`cd src/voice-agent && .venv/bin/python -c "import ast; ast.parse(open('pipeline/automod/finalize.py').read()); print('ok')"`

- [ ] **Step 5: Commit**
```bash
git add bin/jarvis-automod-impl src/voice-agent/pipeline/automod/finalize.py
git commit -m "feat(evolution): capture build total_cost_usd into the cost ledger"
```

---

### Task 3: Redesign the gate (idle + budget + cooldown; drop count cap)

**Files:**
- Modify: `src/voice-agent/pipeline/automod/throttle.py`
- Test: `src/voice-agent/tests/test_automod_throttle.py` (extend; create if absent)

- [ ] **Step 1: Write failing tests for the new gates**
```python
import pipeline.automod.throttle as th

def _intent(text="do x"): return {"intent": text, "proposed_paths_hint": []}

def test_blocks_when_not_idle(monkeypatch):
    monkeypatch.setattr(th, "_idle_seconds", lambda: 30)        # 30s < 600s
    monkeypatch.setattr(th, "_since_last_build_min", lambda: 999)
    monkeypatch.setattr(th, "_budget_spent", lambda: 0.0)
    ok, reason = th.admit_intent(_intent())
    assert not ok and reason == "not_idle"

def test_blocks_when_budget_exhausted(monkeypatch):
    monkeypatch.setattr(th, "_idle_seconds", lambda: 9999)
    monkeypatch.setattr(th, "_since_last_build_min", lambda: 999)
    monkeypatch.setattr(th, "_budget_spent", lambda: 6.0)       # == daily_usd default
    ok, reason = th.admit_intent(_intent())
    assert not ok and reason == "budget_exhausted"

def test_blocks_on_cooldown(monkeypatch):
    monkeypatch.setattr(th, "_idle_seconds", lambda: 9999)
    monkeypatch.setattr(th, "_budget_spent", lambda: 0.0)
    monkeypatch.setattr(th, "_since_last_build_min", lambda: 5)  # < 60
    ok, reason = th.admit_intent(_intent())
    assert not ok and reason == "cooldown"

def test_admits_when_all_pass(monkeypatch):
    monkeypatch.setattr(th, "_idle_seconds", lambda: 9999)
    monkeypatch.setattr(th, "_budget_spent", lambda: 0.0)
    monkeypatch.setattr(th, "_since_last_build_min", lambda: 999)
    ok, reason = th.admit_intent(_intent())
    assert ok and reason == ""
```

- [ ] **Step 2: Run — expect FAIL** (helpers + new reasons don't exist).
`cd src/voice-agent && .venv/bin/python -m pytest tests/test_automod_throttle.py -q`

- [ ] **Step 3: Implement the new gate in `throttle.py`**
Add helpers + rewrite `admit_intent`. Keep `daily_cap()`/count functions for the backstop. Add:
```python
import sqlite3
from pathlib import Path
from pipeline.automod import cost_ledger

IDLE_MIN = lambda: int(os.environ.get("JARVIS_EVOLUTION_IDLE_MIN", "10"))
COOLDOWN_MIN = lambda: int(os.environ.get("JARVIS_EVOLUTION_COOLDOWN_MIN", "60"))

def _idle_seconds() -> float:
    """Seconds since the last voice turn (large if none / db missing = idle)."""
    db = Path.home() / ".local/share/jarvis/turn_telemetry.db"
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        row = con.execute("SELECT (julianday('now')-julianday(MAX(ts_utc)))*86400 FROM turns").fetchone()
        con.close()
        return float(row[0]) if row and row[0] is not None else 1e9
    except Exception:
        return 1e9

def _budget_spent() -> float:
    return cost_ledger.spent_today()

def _since_last_build_min() -> float:
    ts = _read_state().get("last_build_ts")
    if not ts: return 1e9
    return (time.time() - float(ts)) / 60.0
```
Rewrite `admit_intent`:
```python
def admit_intent(intent: dict) -> tuple[bool, str]:
    text = (intent.get("intent") or "").strip()
    if not text:
        return False, "empty_intent"
    for path in (intent.get("proposed_paths_hint") or []):
        if is_blocked_path(path):
            return False, f"blocked_path:{path}"
    if _idle_seconds() < IDLE_MIN() * 60:
        return False, "not_idle"
    if _budget_spent() >= cost_ledger.daily_usd():
        return False, "budget_exhausted"
    if _since_last_build_min() < COOLDOWN_MIN():
        return False, "cooldown"
    # Emergency count backstop: only if explicitly set.
    cap_env = os.environ.get("JARVIS_AUTOMOD_DAILY_CAP")
    if cap_env and _read_state().get("admitted_today", 0) >= daily_cap():
        return False, "daily_cap_reached"
    return True, ""
```
Update `mark_admitted` to also stamp `last_build_ts`:
```python
def mark_admitted(intent_id: str) -> None:
    state = _read_state()
    state["admitted_today"] = state.get("admitted_today", 0) + 1
    state["last_build_ts"] = time.time()
    _write_state(state)
    logger.info("[automod] admitted: id=%s spent_today=$%.2f/%.2f",
                intent_id, _budget_spent(), cost_ledger.daily_usd())
```

- [ ] **Step 4: Run tests — expect PASS.** Same command as Step 2. Then run the existing throttle/spawner tests to confirm no regression: `.venv/bin/python -m pytest tests/ -k "throttle or spawner or automod" -q`.

- [ ] **Step 5: Commit**
```bash
git add src/voice-agent/pipeline/automod/throttle.py src/voice-agent/tests/test_automod_throttle.py
git commit -m "feat(evolution): gate on idle+budget+cooldown, demote count cap to backstop"
```

---

### Task 4: Cadence — 4-hourly build tick

**Files:**
- Modify: `setup/systemd/jarvis-evolution-nightly.timer`
- Apply live (sed → `~/.config/systemd/user/`, daemon-reload)

- [ ] **Step 1: Change the timer**
Replace the `[Timer]` `OnUnitActiveSec=30min` line (keep `OnBootSec`) with:
```ini
OnCalendar=*-*-* 00/04/08/12/16/20:00:00
Persistent=false
RandomizedDelaySec=300s
```
Update the unit `Description`/comments to say "every 4 hours" not "30 min".

- [ ] **Step 2: Validate + apply live**
```bash
systemd-analyze --user verify setup/systemd/jarvis-evolution-nightly.timer 2>&1 | grep -iE 'error|invalid' || echo ok
INSTALL_DIR="$PWD"; sed -e "s|%h/Documents/Projects/jarvis|$INSTALL_DIR|g" -e "s|/home/[^/]*/Documents/Projects/jarvis|$INSTALL_DIR|g" -e "s|/home/[^/]*/jarvis|$INSTALL_DIR|g" setup/systemd/jarvis-evolution-nightly.timer > ~/.config/systemd/user/jarvis-evolution-nightly.timer
systemctl --user daemon-reload
systemctl --user list-timers --all | grep evolution-nightly   # NEXT should be a 4h boundary
```

- [ ] **Step 3: Commit**
```bash
git add setup/systemd/jarvis-evolution-nightly.timer
git commit -m "feat(evolution): build tick every 4h (was every 30min)"
```

---

### Task 5: Dashboard queue-depth fix

**Files:**
- Modify: `src/web/src/hooks/use-evolution-count.ts`
- Possibly Modify: `src/web/src/app/api/evolution/route.ts` (only if `readQueue()` under-counts)

- [ ] **Step 1: Runtime-diagnose the count**
With the web dev server running (or against prod): `curl -s localhost:3000/api/evolution | python3 -c 'import sys,json; d=json.load(sys.stdin); print("status.queued=",d["status"]["queued"],"queued.len=",len(d["queued"]),"proposals.len=",len(d["proposals"]))'`
Compare to `wc -l < ~/.jarvis/auto-mods/queue.jsonl` (17). Note whether `status.queued` matches.

- [ ] **Step 2: Point the badge at the queue**
In `use-evolution-count.ts`, change the return to read the queue, not proposals:
```ts
type EvolutionResp = { status?: { queued?: number } };
// ...
return data?.status?.queued ?? 0;
```
(Update the `EvolutionList` type accordingly.)

- [ ] **Step 3: If Step 1 showed `status.queued` < real depth, fix `readQueue()`**
In `route.ts`, `readQueue()` reads `queue.jsonl` tail-50 + dedup. If it drops live intents, widen the tail-read (read all lines, not 50) and ensure dedup keys on `id` only (not status). Show the corrected count matches `wc -l` minus dismissed/dead intents. (If Step 1 showed it already matches, skip this step.)

- [ ] **Step 4: Verify web build**
`cd src/web && npx tsc --noEmit 2>&1 | tail -3 && echo tsc-ok`

- [ ] **Step 5: Commit**
```bash
git add src/web/src/hooks/use-evolution-count.ts src/web/src/app/api/evolution/route.ts
git commit -m "fix(web/evolution): badge + count show real queue depth, not proposals"
```

---

### Task 6: Wire config + remove the old cap default from the live env

**Files:**
- Modify: `setup/systemd/jarvis-voice-agent.service` (the env block) + apply live (daemon-reload, NO restart unless idle)

- [ ] **Step 1: Add the budget/idle/cooldown env (optional — defaults are in code)**
The defaults ($6 / 10min / 60min) live in code, so this is only to make them visible/tunable. Add to the `[Service]` env block:
```ini
Environment=JARVIS_EVOLUTION_DAILY_USD=6
Environment=JARVIS_EVOLUTION_IDLE_MIN=10
Environment=JARVIS_EVOLUTION_COOLDOWN_MIN=60
```
Do NOT set `JARVIS_AUTOMOD_DAILY_CAP` (leave the count backstop off). Do NOT touch the other env lines.

- [ ] **Step 2: Apply live (no restart — loop is paused anyway)**
```bash
INSTALL_DIR="$PWD"; sed -e "s|%h/Documents/Projects/jarvis|$INSTALL_DIR|g" -e "s|/home/[^/]*/Documents/Projects/jarvis|$INSTALL_DIR|g" -e "s|/home/[^/]*/jarvis|$INSTALL_DIR|g" setup/systemd/jarvis-voice-agent.service > ~/.config/systemd/user/jarvis-voice-agent.service
systemctl --user daemon-reload
```

- [ ] **Step 3: Commit**
```bash
git add setup/systemd/jarvis-voice-agent.service
git commit -m "chore(evolution): surface budget/idle/cooldown env on the unit"
```

---

### Task 7: Shadow-soak, then unpause

- [ ] **Step 1: Full suite green**
`cd src/voice-agent && ../../bin/jarvis-automod-pytest tests/ -q` → all pass.

- [ ] **Step 2: Dry-run the gate (still paused)**
`cd src/voice-agent && .venv/bin/python -c "from pipeline.automod import throttle, cost_ledger; print('idle_s', throttle._idle_seconds()); print('spent', cost_ledger.spent_today(), 'of', cost_ledger.daily_usd()); print(throttle.admit_intent({'intent':'noop','proposed_paths_hint':[]}))"`
Confirm the reason matches reality (e.g. `not_idle` if you're mid-session, or admit if idle+budget OK).

- [ ] **Step 3: Reset today's stale count + clear the pause**
```bash
# the 18-count from the old cap is meaningless now; reset state cleanly
python3 -c "import json,os; p=os.path.expanduser('~/.jarvis/auto-mods/throttle.json'); json.dump({'date':__import__('time').strftime('%Y-%m-%d',__import__('time').gmtime()),'admitted_today':0}, open(p,'w'))"
rm -f ~/.jarvis/auto-mods/.evolution-paused
```

- [ ] **Step 4: Watch one real 4h tick**
After the next `jarvis-evolution-nightly` fire (or trigger `systemctl --user start jarvis-evolution-nightly.service`), check the log: a build should fire only if idle+budget+cooldown pass, and `cost-ledger.json` should gain an entry. `tail ~/.local/share/jarvis/logs/voice-agent.log | grep automod` + `cat ~/.jarvis/auto-mods/cost-ledger.json`.

- [ ] **Step 5: Open the PR** (whole branch). Title `feat(evolution): cost-budget governance + 4h cadence + dashboard fix`.

---

## Self-review

- **Spec coverage:** budget brake (T1+T2), idle/cooldown/signal gate (T3), cadence 4h+nightly (T4), dashboard (T5), config (T6), unpause/soak (T7), human-approve-deploy + blocklist + watchdog (untouched, per spec). ✓
- **Placeholders:** none — full code for ledger + gate + tests; Step-1 verifications (cost field name, queue count) are explicit diagnose-then-act, not "TBD". ✓
- **Type consistency:** `cost_ledger.record/spent_today/daily_usd`, `throttle._idle_seconds/_budget_spent/_since_last_build_min/admit_intent`, badge `status.queued` — consistent across tasks. ✓
