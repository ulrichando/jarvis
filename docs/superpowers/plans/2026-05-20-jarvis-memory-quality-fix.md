# JARVIS Memory Quality Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop JARVIS's long-term memory store from being polluted by ambient TV/media audio and from pinning garbage into every prompt, by (1) breaking the `use_count` injection feedback loop, (2) gating memory writes on engagement, and (3) cleaning the existing store.

**Architecture:** Three independent, sequenced changes. Part 1 (read-side) changes injection ranking from a self-reinforcing `use_count` counter to recency and removes the inject→bump side effect — ships alone, lowest risk. Part 2 (write-side, the root-cause fix) gates the memory extractor + capture-trigger behind a vocative-armed engagement window so cold ambient turns are never written. Part 3 is a human-reviewed one-time purge of the existing pollution.

**Tech Stack:** Python 3.13 (voice-agent, its own `.venv`), pytest, SQLite (`~/.jarvis/hub/state.db`), the hub event bus (Redis Streams → `src/hub/server.py`), TypeScript/Bun (hub client twin).

---

## Background — root cause (evidence-backed, 2026-05-20 spike)

Forensic analysis of the live `state.db` (495 memories, 2,279 messages):

- **Pollution:** ~22% of memories are Whisper hallucinations of ambient TV/game audio extracted as "facts" (e.g. *"The Wimah is a fictional currency in this world"*, *"Transcription is provided by CastingWords"*). Every one traces 1:1 to a `role='user'` message — STT transcribed ambient audio as user speech, and the extractor faithfully extracted from it.
- **Ambient ingestion is large:** 9 sessions with >30 user turns and <20% reply rate account for **594/1,709 user turns** (e.g. `3834faa5` = 171 user / 4 assistant over 37 min — a hot mic eating a TV show).
- **The only ambient defense** (`jarvis_agent.py:3820` quiet-hours gate) is time-boxed to 23:00–07:00 AND defeated by `_recent_interaction()`. The extractor (`jarvis_agent.py:3936`) + capture-trigger (`:3896`) fire on every surviving turn with no "addressed to JARVIS" check. Hardening the extractor LLM **cannot** fix this: *"My name is Yonichiichi"* (TV dub) is structurally identical to a real *"my name is Ulrich"*. The fix must be at the gate.
- **`use_count` is a rich-get-richer lock-in:** `format_memories_for_prompt` (`tools/memory.py:432`) reads the top-8 by `use_count DESC` then **bumps those same 8**. 469/495 sit at 0; the 8 pinned at ~2358 share one batch `last_used_ts` and are garbage from a May 15–16 session.

## Scope

```
SCOPE:   src/hub/client.py (ranking SQL)
         src/hub/client.ts (ranking SQL — desktop/web parity)
         src/voice-agent/tools/memory.py (remove inject-bump)
         src/voice-agent/pipeline/memory_gate.py (NEW — engagement gate)
         src/voice-agent/jarvis_agent.py (wire gate into on_user_turn_completed)
         src/voice-agent/pipeline/memory_extractor.py (optional SKIP-prompt hardening)
         bin/jarvis-memory-purge (NEW — review-based cleanup)
         tests under src/voice-agent/tests/
OUT:     STT / AEC / hot-mic / VAD ingestion path (Layer 0)
         the supervisor reply path (what JARVIS responds to)
         state.db schema (NO trust_score column in this plan)
         the memory consolidator, src/cli/, src/desktop-tauri/ UI, the hermes/ checkout
WHY OUT: Ingesting ambient audio at the mic is the true root but it is the
         subject of ACTIVE AEC work in the git log (_HOT_MIC_SET=l1) — fixing
         memory at the write gate does not require touching it, and colliding
         risks regressing barge-in. A trust_score column is the proper long-term
         ranking signal but needs a feedback-source design JARVIS lacks today
         (no thumbs-up/down on memories) — deferred to a follow-up, not bolted on.
```

## Design decisions

1. **Vocative-anchored window, NOT assistant-reply-anchored.** The spike found JARVIS sometimes *replied* to ambient TV (171u/4a session), so anchoring the window on a reply would re-open it for ambient audio. Ambient TV almost never says "Jarvis", so the vocative is the robust anchor. A 180 s rolling window covers conversational follow-ups that drop the vocative. *(This refines the "lenient/assistant-reply" framing from the pre-plan discussion — the binge data showed reply-anchoring leaks.)*
2. **Gate WRITES, not REPLIES.** Smallest blast radius that fixes memory: it changes only what JARVIS permanently *remembers*, not what it *says*. Lowest UX risk and avoids the AEC layer.
3. **Recency ranking for v1 (no schema change).** Killing the loop means `use_count` can no longer be the sort key (the garbage is pinned there). Pure `updated_ts DESC` is unbiased and ships without a migration. **Known tradeoff:** core old facts (e.g. wife's name) can fall out of the injected top-8 once 8 newer facts exist; they remain reachable via the explicit `recall()` path. The proper fix (trust/importance ranking that keeps core facts pinned) is the deferred follow-up below.
4. **Kill-switches everywhere.** `JARVIS_MEMORY_ENGAGEMENT_GATE=0` restores legacy extract-on-every-turn; `JARVIS_MEMORY_ENGAGEMENT_WINDOW_S` tunes the window live.
5. **Cleanup is human-in-the-loop.** Heuristic flagging is imperfect (some no-self proper-noun facts are real third parties). Dry-run → reviewable kill-list → explicit `--apply` → backup first. Never auto-delete user data.

## File structure

| File | Responsibility |
|---|---|
| `src/hub/client.py` | Python read path used by the voice agent; ranking SQL (Part 1) |
| `src/hub/client.ts` | TS twin for desktop/web; same ranking change |
| `src/voice-agent/tools/memory.py` | `format_memories_for_prompt` — remove the inject-bump side effect |
| `src/voice-agent/pipeline/memory_gate.py` | **NEW** — pure, testable vocative-window gate for memory writes |
| `src/voice-agent/jarvis_agent.py` | `on_user_turn_completed` — arm window on vocative, guard both writers |
| `bin/jarvis-memory-purge` | **NEW** — dry-run flagging + `--apply` removal via the hub bus |

---

## Part 1 — Break the `use_count` injection loop (ships independently)

### Task 1: Rank injected memories by recency (Python hub client)

**Files:**
- Modify: `src/hub/client.py:111`
- Test: `src/voice-agent/tests/test_hub_client_memory_read.py:44-47`

- [ ] **Step 1: Update the existing test to assert recency order**

In `test_hub_client_memory_read.py`, the seeded rows are m1 (`updated_ts=now-1000`), m2 (`now-2000`), m3 (`now-3000`). Replace the use_count-order test:

```python
def test_read_memories_orders_by_recency(seeded_db):
    out = hub_client.HubClient.read_memories_sync(limit=10)
    # Ranked by updated_ts DESC now (use_count is no longer the sort key —
    # it was a self-reinforcing inject→bump loop, see 2026-05-20 memory fix).
    # m1 newest (now-1000), then m2 (now-2000), then m3 (now-3000).
    assert [m["memory_id"] for m in out] == ["m1", "m2", "m3"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_hub_client_memory_read.py::test_read_memories_orders_by_recency -v`
Expected: FAIL — current SQL returns `["m1", "m3", "m2"]` (m3 has use_count=2, m2 has 0).

- [ ] **Step 3: Change the ranking SQL**

In `src/hub/client.py:111`, change:

```python
        sql += "ORDER BY use_count DESC, updated_ts DESC LIMIT ?"
```
to:
```python
        # Recency, not use_count: use_count was a self-reinforcing inject→bump
        # loop that pinned the earliest-injected (garbage) memories forever.
        # See docs/superpowers/plans/2026-05-20-jarvis-memory-quality-fix.md.
        sql += "ORDER BY updated_ts DESC LIMIT ?"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_hub_client_memory_read.py -v`
Expected: PASS (all tests in the file).

- [ ] **Step 5: Commit**

```bash
git add src/hub/client.py src/voice-agent/tests/test_hub_client_memory_read.py
git commit -m "fix(memory): rank injected memories by recency, not the use_count lock-in"
```

### Task 2: Remove the self-reinforcing inject-bump

**Files:**
- Modify: `src/voice-agent/tools/memory.py:431-434`
- Test: `src/voice-agent/tests/test_memory_injection_no_bump.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# src/voice-agent/tests/test_memory_injection_no_bump.py
"""format_memories_for_prompt must NOT mutate use_count — injecting a
memory into the prompt is not evidence it was useful. (2026-05-20 fix
for the inject→bump rich-get-richer loop.)"""
import sqlite3
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "hub"))
import server  # noqa: E402


@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
    db = tmp_path / "state.db"
    server.bootstrap_schema(db)
    monkeypatch.setenv("JARVIS_HUB_DB", str(db))
    now = int(time.time() * 1000)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO memories (memory_id, content, category, source, "
        "source_session_id, created_ts, updated_ts, last_used_ts, use_count) "
        "VALUES ('m1','Ulrich runs Pretva','user','voice',NULL,?,?,NULL,0)",
        (now, now),
    )
    conn.commit()
    conn.close()
    return db


def test_format_for_prompt_does_not_bump_use_count(seeded_db):
    from tools.memory import format_memories_for_prompt
    format_memories_for_prompt(top_n=8)
    conn = sqlite3.connect(seeded_db)
    uc = conn.execute("SELECT use_count FROM memories WHERE memory_id='m1'").fetchone()[0]
    conn.close()
    assert uc == 0, "injection must not increment use_count"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_memory_injection_no_bump.py -v`
Expected: FAIL — `use_count == 1` (the current `_bump_uses_via_sdk` call bumps it).

- [ ] **Step 3: Remove the bump block**

In `src/voice-agent/tools/memory.py`, delete the bump at lines 431-434:

```python
    try:
        _bump_uses_via_sdk([r["memory_id"] for r in rows])
    except Exception as e:
        logger.warning("[memory] bump failed: %s", e)
    return block
```
becomes:
```python
    # NOTE: do NOT bump use_count here. Injecting a memory into the prompt
    # is not evidence it was useful; the old bump created a self-reinforcing
    # loop that pinned the earliest-injected garbage forever (2026-05-20 fix).
    # bump_memory_use_sync is retained in the hub client for a future
    # genuine-usefulness signal (explicit query recall), not blanket injection.
    return block
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_memory_injection_no_bump.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/tools/memory.py src/voice-agent/tests/test_memory_injection_no_bump.py
git commit -m "fix(memory): stop bumping use_count on prompt injection (kills the feedback loop)"
```

### Task 3: Mirror recency ranking in the TS hub client (desktop/web parity)

**Files:**
- Modify: `src/hub/client.ts:140`

- [ ] **Step 1: Change the ranking SQL**

In `src/hub/client.ts:140`, change:

```typescript
      sql += 'ORDER BY use_count DESC, updated_ts DESC LIMIT ?'
```
to:
```typescript
      // Recency, not use_count — see voice-agent fix 2026-05-20.
      sql += 'ORDER BY updated_ts DESC LIMIT ?'
```

- [ ] **Step 2: Typecheck the hub**

Run: `cd src/hub && bunx tsc --noEmit`
Expected: no errors. (If the hub has a test runner, also run `bun test`.)

- [ ] **Step 3: Commit**

```bash
git add src/hub/client.ts
git commit -m "fix(hub): rank memories by recency in the TS client (parity with python)"
```

---

## Part 2 — Engagement gate on memory writes (root-cause fix)

### Task 4: Create the engagement-gate module

**Files:**
- Create: `src/voice-agent/pipeline/memory_gate.py`
- Test: `src/voice-agent/tests/test_memory_gate.py`

- [ ] **Step 1: Write the failing test**

```python
# src/voice-agent/tests/test_memory_gate.py
"""Vocative-armed engagement gate for memory WRITES (2026-05-20
ambient-pollution fix). Cold ambient turns (no recent 'Jarvis')
must not be written to long-term memory."""
import pytest
from pipeline import memory_gate


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.delenv("JARVIS_MEMORY_ENGAGEMENT_GATE", raising=False)
    monkeypatch.delenv("JARVIS_MEMORY_ENGAGEMENT_WINDOW_S", raising=False)
    memory_gate.reset()
    yield
    memory_gate.reset()


def test_cold_turn_no_vocative_is_not_engaged():
    assert memory_gate.is_write_engaged(now=1000.0) is False


def test_armed_within_window_is_engaged():
    memory_gate.note_vocative(now=1000.0)
    assert memory_gate.is_write_engaged(now=1100.0, window_s=180.0) is True


def test_armed_past_window_is_not_engaged():
    memory_gate.note_vocative(now=1000.0)
    assert memory_gate.is_write_engaged(now=1200.0, window_s=180.0) is False


def test_kill_switch_forces_engaged(monkeypatch):
    monkeypatch.setenv("JARVIS_MEMORY_ENGAGEMENT_GATE", "0")
    assert memory_gate.is_write_engaged(now=1000.0) is True  # no vocative needed


def test_window_from_env(monkeypatch):
    monkeypatch.setenv("JARVIS_MEMORY_ENGAGEMENT_WINDOW_S", "60")
    memory_gate.note_vocative(now=1000.0)
    assert memory_gate.is_write_engaged(now=1059.0) is True
    assert memory_gate.is_write_engaged(now=1061.0) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_memory_gate.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline.memory_gate'`.

- [ ] **Step 3: Write the module**

```python
# src/voice-agent/pipeline/memory_gate.py
"""Engagement gate for memory WRITES.

Root cause (2026-05-20 spike): the hot mic transcribes ambient TV/media
audio into coherent role='user' turns; the extractor + capture-trigger
fire on them, polluting state.db.memories (~22% of the store was ambient
hallucinations). The only prior defense (quiet-hours gate) was time-boxed
and defeated by _recent_interaction(), so daytime ambient sailed through.

This gate arms a rolling window on a 'Jarvis' vocative and only allows
memory writes inside it. Ambient TV almost never says 'Jarvis', so cold
ambient turns are refused. Anchored on the vocative, NOT an assistant
reply — the spike found JARVIS sometimes replied to ambient TV, so a reply
anchor would re-open the window for ambient audio.

Gates WRITES only (extractor + capture-trigger); does NOT change what
JARVIS replies to. Kill-switch: JARVIS_MEMORY_ENGAGEMENT_GATE=0.
"""
from __future__ import annotations

import os
import time

_LAST_VOCATIVE_AT: float | None = None
_DEFAULT_WINDOW_S = 180.0


def _gate_enabled() -> bool:
    return os.environ.get("JARVIS_MEMORY_ENGAGEMENT_GATE", "1") != "0"


def _window_s() -> float:
    try:
        return float(os.environ.get("JARVIS_MEMORY_ENGAGEMENT_WINDOW_S",
                                    str(_DEFAULT_WINDOW_S)))
    except (TypeError, ValueError):
        return _DEFAULT_WINDOW_S


def note_vocative(now: float | None = None) -> None:
    """Arm the engagement window. Call when a turn contains a 'Jarvis'
    vocative (or a deliberate wake phrase)."""
    global _LAST_VOCATIVE_AT
    _LAST_VOCATIVE_AT = time.monotonic() if now is None else now


def is_write_engaged(now: float | None = None,
                     window_s: float | None = None) -> bool:
    """True if a memory WRITE should be allowed this turn. Gate disabled
    (env=0) => always True (legacy). Else True iff a vocative armed the
    window within window_s."""
    if not _gate_enabled():
        return True
    if _LAST_VOCATIVE_AT is None:
        return False
    now = time.monotonic() if now is None else now
    window_s = _window_s() if window_s is None else window_s
    return (now - _LAST_VOCATIVE_AT) <= window_s


def reset() -> None:
    """Test seam — clear the armed window."""
    global _LAST_VOCATIVE_AT
    _LAST_VOCATIVE_AT = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_memory_gate.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/pipeline/memory_gate.py src/voice-agent/tests/test_memory_gate.py
git commit -m "feat(memory): vocative-armed engagement gate for memory writes"
```

### Task 5: Wire the gate into `on_user_turn_completed`

**Files:**
- Modify: `src/voice-agent/jarvis_agent.py` (module import near :490; arm near :3774; guard near :3896 and :3936)

> No new automated test: this is thin glue inside a 5000-line async method whose isolation would require mocking the whole `Agent`. Logic is covered by Task 4's unit tests; this task is verified by the full suite staying green + a manual soak (Step 5).

- [ ] **Step 1: Add the module import**

Near `src/voice-agent/jarvis_agent.py:490` (where `NAME_RE as _JARVIS_NAME_RE` is imported), add:

```python
from pipeline.memory_gate import note_vocative as _note_vocative, is_write_engaged as _is_write_engaged
```

- [ ] **Step 2: Arm the window on a vocative**

In `on_user_turn_completed`, immediately AFTER the garbage gate (after the `raise StopResponse()` at line ~3773, before the `if _is_silent():` block), insert:

```python
        # Arm the memory-write engagement window on any 'Jarvis' vocative.
        # Cold ambient audio (TV/media the hot mic transcribes) lacks the
        # vocative, so it is never written to long-term memory. Placed before
        # the silent/mute early-returns so a wake turn arms the window for the
        # following content turn. See pipeline/memory_gate.py (2026-05-20).
        if _JARVIS_NAME_RE.search(text):
            _note_vocative()
```

- [ ] **Step 3: Guard the capture-trigger writer**

In the Layer-1.5 block, wrap the capture-trigger detection (line ~3896). Change:

```python
            try:
                trigger = detect_capture_trigger(text)
                if trigger is not None:
```
to:
```python
            try:
                trigger = detect_capture_trigger(text) if _is_write_engaged() else None
                if trigger is not None:
```

- [ ] **Step 4: Guard the extractor writer**

Change the extractor dispatch (line ~3936):

```python
            # Don't await — the extractor must NOT block the supervisor reply.
            _asyncio.create_task(_run_extractor_and_publish(text))
```
to:
```python
            # Don't await — the extractor must NOT block the supervisor reply.
            # Gate on engagement: only write memory for vocative-armed turns,
            # never cold ambient audio (2026-05-20 ambient-pollution fix).
            if _is_write_engaged():
                _asyncio.create_task(_run_extractor_and_publish(text))
            else:
                logger.info(f"[memory-gate] write skipped (no recent vocative): {text[:60]!r}")
```

- [ ] **Step 5: Verify — full suite + manual soak**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/`
Expected: all green (no regressions).

Then (respecting the restart rule — check `~/.local/share/jarvis/turn_telemetry.db` latest `ts_utc` is >60 s old first):
```bash
systemctl --user restart jarvis-voice-agent.service
tail -f ~/.local/share/jarvis/logs/voice-agent.log | grep -E "memory-gate|extractor"
```
Confirm: speaking WITHOUT "Jarvis" (cold) → `[memory-gate] write skipped`; saying "Jarvis, my dentist is Dr. Kim" → `[extractor] user: ...`.

- [ ] **Step 6: Commit**

```bash
git add src/voice-agent/jarvis_agent.py
git commit -m "feat(memory): gate extractor + capture-trigger on vocative engagement window"
```

### Task 6 (optional, defense-in-depth): harden the extractor SKIP prompt

**Files:**
- Modify: `src/voice-agent/pipeline/memory_extractor.py` (`_EXTRACTOR_PROMPT`, the ANTI-EXAMPLES block ~line 237)

> Not TDD — this is a prompt string with no testable logic change (the pure `parse_extractor_output` is unchanged). Verified by soak, and it is a backstop, NOT the fix (Part 2 is). Catches third-person fiction the gate's window still lets through during a real session.

- [ ] **Step 1: Add fiction/narrative anti-examples**

In the `ANTI-EXAMPLES` section of `_EXTRACTOR_PROMPT`, append:

```
  ✗ "The Wimah is a fictional currency in this world" — fiction/world-building
  ✗ "X causes damage once per day" — game/anime mechanics
  ✗ "<Name> says, the first episode of <Show> was good" — TV/media narration
  ✗ "Transcription by/provided by <service>" — caption-credit boilerplate
```

- [ ] **Step 2: Verify parse tests unaffected + commit**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_memory_extractor.py -v`
Expected: PASS (unchanged — prompt-only edit).
```bash
git add src/voice-agent/pipeline/memory_extractor.py
git commit -m "chore(memory): add fiction/TV anti-examples to extractor SKIP prompt"
```

---

## Part 3 — Review-based cleanup of existing pollution

### Task 7: Build the dry-run purge tool

**Files:**
- Create: `bin/jarvis-memory-purge`
- Test: `src/voice-agent/tests/test_memory_purge.py`

- [ ] **Step 1: Write the failing test for the pure flagging function**

```python
# src/voice-agent/tests/test_memory_purge.py
"""flag_garbage must catch ambient-audio hallucinations and LLM
narration while sparing real first-person facts. CONSERVATIVE — output
feeds a human-reviewed dry-run, never auto-deletion."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "jarvis_memory_purge",
    Path(__file__).parent.parent.parent.parent / "bin" / "jarvis-memory-purge",
)
purge = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(purge)


def test_flags_ambient_and_narration_spares_real_facts():
    mems = [
        {"memory_id": "g1", "content": "The Wimah is a fictional currency in this world."},
        {"memory_id": "g2", "content": "Gargis is being greeted."},
        {"memory_id": "g3", "content": "The user appears to be asking about something."},
        {"memory_id": "k1", "content": "Ulrich's wife is named Lizzy."},
        {"memory_id": "k2", "content": "User prefers terse replies."},
    ]
    flagged_ids = {m["memory_id"] for m in purge.flag_garbage(mems)}
    assert {"g1", "g2", "g3"} <= flagged_ids       # caught
    assert "k1" not in flagged_ids and "k2" not in flagged_ids  # spared
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_memory_purge.py -v`
Expected: FAIL — `bin/jarvis-memory-purge` does not exist.

- [ ] **Step 3: Write the tool**

```python
#!/usr/bin/env python3
"""jarvis-memory-purge — review-based cleanup of ambient-audio pollution
in ~/.jarvis/hub/state.db.memories (2026-05-20 fix).

Default: DRY-RUN. Flags likely-garbage memories and writes them to
~/.jarvis/memory-purge-candidates.txt for human review (edit/trim it).
--apply: backs up state.db, then publishes memory.value.removed events
(via the hub bus) for the memory_ids in the reviewed kill-list file.
Never deletes without --apply.
"""
from __future__ import annotations

import argparse
import re
import shutil
import sqlite3
import sys
import time
from pathlib import Path

_STATE_DB = Path.home() / ".jarvis" / "hub" / "state.db"
_CANDIDATES = Path.home() / ".jarvis" / "memory-purge-candidates.txt"

_SELF = re.compile(r"\b(ulrich|user|jarvis|i|i'm|im|my|me|we|our|you|your)\b", re.I)
_META = re.compile(r"""(?ix)^\s*(
      the\s+(user|conversation|speaker|discussion|topic|exchange)\b
    | it\s+(seems|appears|looks|sounds)\b
    | this\s+(is|seems|appears)\b )""")
_PROPER = re.compile(r"\b([A-Z][a-z]{2,})\b")
_WHITELIST = {"The", "User", "Ulrich", "Jarvis", "Chrome", "It", "This",
              "That", "A", "An", "I", "Dr", "Mr", "Mrs", "Africa",
              "Senegal", "Japanese"}


def _is_meta(c: str) -> bool:
    return bool(_META.search(c))


def _is_tv_narrative(c: str) -> bool:
    if _SELF.search(c):
        return False
    props = [p for p in _PROPER.findall(c) if p not in _WHITELIST]
    return len(props) >= 1


def flag_garbage(memories: list[dict]) -> list[dict]:
    """Return the subset that looks like ambient-audio hallucination or
    LLM narration. Conservative; for human review, not auto-deletion."""
    return [m for m in memories
            if _is_meta(m["content"]) or _is_tv_narrative(m["content"])]


def _read_all() -> list[dict]:
    conn = sqlite3.connect(f"file:{_STATE_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(
            "SELECT memory_id, content, category FROM memories").fetchall()]
    finally:
        conn.close()


def _dry_run() -> None:
    mems = _read_all()
    flagged = flag_garbage(mems)
    _CANDIDATES.write_text(
        "\n".join(f"{m['memory_id']}\t{m['content']}" for m in flagged),
        encoding="utf-8",
    )
    print(f"{len(flagged)}/{len(mems)} memories flagged as likely garbage.")
    print(f"Review/edit the kill-list, then re-run with --apply:")
    print(f"  {_CANDIDATES}")


def _apply() -> None:
    if not _CANDIDATES.exists():
        sys.exit("No candidates file — run a dry-run first.")
    ids = [ln.split("\t", 1)[0].strip()
           for ln in _CANDIDATES.read_text(encoding="utf-8").splitlines()
           if ln.strip()]
    if not ids:
        sys.exit("Kill-list empty — nothing to remove.")
    backup = _STATE_DB.with_suffix(f".db.bak-{int(time.time())}")
    shutil.copy2(_STATE_DB, backup)
    print(f"Backed up state.db -> {backup}")
    # Publish removal events through the hub bus so the daemon applies the
    # DELETE consistently (src/hub/server.py: memory.value.removed).
    sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "voice-agent"))
    from tools.memory import _publish_event  # type: ignore
    for mid in ids:
        _publish_event("memory.value.removed", {"memory_id": mid})
    print(f"Published {len(ids)} memory.value.removed events.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="remove the reviewed kill-list (default: dry-run)")
    args = ap.parse_args()
    _apply() if args.apply else _dry_run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes; make executable**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_memory_purge.py -v`
Expected: PASS.
```bash
chmod +x bin/jarvis-memory-purge
```

- [ ] **Step 5: Commit**

```bash
git add bin/jarvis-memory-purge src/voice-agent/tests/test_memory_purge.py
git commit -m "feat(memory): review-based purge tool for ambient-audio pollution"
```

### Task 8: Run the cleanup (operational runbook — after Parts 1 & 2 are live)

> Manual, destructive. Do this only after Part 2 is deployed, so the store stops refilling. Verify the hub is running (`systemctl --user status jarvis-hub.service`). Run with the voice-agent venv — `--apply` lazily imports `tools.memory` (which pulls in livekit), so system python will fail on that path.

- [ ] **Step 1: Dry-run and review**

```bash
PY=src/voice-agent/.venv/bin/python
$PY bin/jarvis-memory-purge
$EDITOR ~/.jarvis/memory-purge-candidates.txt   # delete any line that is actually a real fact
```

- [ ] **Step 2: Apply and verify the count drop**

```bash
PY=src/voice-agent/.venv/bin/python
sqlite3 ~/.jarvis/hub/state.db "SELECT COUNT(*) FROM memories;"   # before
$PY bin/jarvis-memory-purge --apply
sleep 3
sqlite3 ~/.jarvis/hub/state.db "SELECT COUNT(*) FROM memories;"   # after — should drop by ~kill-list size
```

---

## Deferred follow-up (NOT in this plan)

**Trust/importance ranking** (the proper fix for "core old facts fall out of the injected top-8"): add a `trust_score` column to `memories`, seed it by source (deterministic capture-trigger > LLM extractor), and bump it on *genuine query recall* (not blanket injection). Needs a `state.db` schema migration (hub + TS writer) and a feedback-source design — out of scope here. Tracked against the Hermes comparative review (`project_memory_store_quality_findings`).

---

## Self-review

- **Spec coverage:** Part 1 (loop) → Tasks 1–3; Part 2 (root-cause write gate) → Tasks 4–6; Part 3 (cleanup) → Tasks 7–8. The deferred trust-ranking is explicitly out of scope with rationale. ✓
- **Placeholder scan:** every code step has complete code; commands have expected output. No TODO/TBD. ✓
- **Type/name consistency:** `note_vocative` / `is_write_engaged` / `reset` used identically in module (Task 4) and wiring (Task 5); `flag_garbage` defined and tested with the same signature (Task 7); `_publish_event` is the existing sync publisher in `tools/memory.py` (verified, :120). The recency SQL string matches in client.py (Task 1) and client.ts (Task 3). ✓
- **Known limitation flagged:** recency-only injection can drop core old facts from the top-8 (design decision #3) — accepted for v1, trust-ranking is the deferred fix. ✓
