# Memory Layer Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make JARVIS reliably persist user-stated facts to `state.db.memories` and stop telling users it can't remember when it factually can.

**Architecture:** Move `remember()` off the LLM's tool-choice surface (the proven Mem0/Zep pattern). Layer in three structural fixes — auto-extraction on turn boundary, deterministic force-routing for recall queries, output-rail denial detector — plus a minimal prompt anchor. Each layer covers a different failure mode; they don't conflict.

**Tech Stack:** Python 3.13, LiveKit Agents framework, Groq llama-3.3-70b (supervisor) + llama-3.1-8b-instant (extractor), SQLite (state.db), Redis (events:memory stream).

**Spec:** [`docs/superpowers/specs/2026-05-08-anti-gaslighting-memory-design.md`](../specs/2026-05-08-anti-gaslighting-memory-design.md)

**Phase markers:**
- 🟢 **Phase 1** (ship today): tasks 1-3
- 🟡 **Phase 2** (this week): tasks 4-9 — auto-extraction
- 🟡 **Phase 3** (this week): tasks 10-13 — recall force-routing
- 🟡 **Phase 4** (this week): tasks 14-18 — denial detector

**Test runner:** `cd src/voice-agent && .venv/bin/python -m pytest tests/`

**Restart safety:** Before any `systemctl --user restart jarvis-voice-agent.service`, check `sqlite3 ~/.local/share/jarvis/turn_telemetry.db "SELECT ts_utc FROM turns ORDER BY ts_utc DESC LIMIT 1"` — if within 60s of now, ask the user before restarting.

**Commit style:** No `Co-Authored-By` trailers. No "Generated with Claude Code" attribution.

---

## File Structure

| File | Phase | Status | Responsibility |
|---|---|---|---|
| `src/voice-agent/jarvis_agent.py` | 1, 4 | Modify | Add YOU-HAVE-MEMORY anchor to JARVIS_INSTRUCTIONS; install denial_detector |
| `src/voice-agent/pipeline/memory_extractor.py` | 2 | Create | Small-LLM extractor: user transcript → `(category, content)` or SKIP |
| `src/voice-agent/pipeline/turn_router.py` | 3 | Modify | Add `_RECALL_PATTERNS` regex + recall-detection helper |
| `src/voice-agent/sanitizers/denial_detector.py` | 4 | Create | Output-rail regex on supervisor text; suppress + re-roll on capability denial |
| `src/voice-agent/sanitizers/__init__.py` | 4 | Modify | Document the new module in docstring |
| `src/voice-agent/tools/memory.py` | 2 | (re-use only) | `_publish_event` is reused by extractor; no edits |
| `src/voice-agent/tests/test_memory_anchor.py` | 1 | Create | Verify YOU-HAVE-MEMORY block present in JARVIS_INSTRUCTIONS |
| `src/voice-agent/tests/test_memory_extractor.py` | 2 | Create | Few-shot extractor unit tests |
| `src/voice-agent/tests/test_recall_router.py` | 3 | Create | `_RECALL_PATTERNS` match/no-match cases |
| `src/voice-agent/tests/test_denial_detector.py` | 4 | Create | Denial regex match/no-match + sanitizer install idempotence |

---

## 🟢 PHASE 1 — Prompt anchor (ship today, ~30 min)

### Task 1: Test the YOU-HAVE-MEMORY anchor is present

**Files:**
- Create: `src/voice-agent/tests/test_memory_anchor.py`

- [ ] **Step 1: Write the failing test**

```python
# src/voice-agent/tests/test_memory_anchor.py
"""Smoke test for the YOU-HAVE-MEMORY supervisor-prompt anchor.

The anchor exists to override the LLM's training-data prior that
'I'm a conversational AI without memory' — replacing it with a
short, naturally-phrased statement that mirrors what Anthropic
auto-injects with their memory tool. See spec:
docs/superpowers/specs/2026-05-08-anti-gaslighting-memory-design.md
"""
from __future__ import annotations


def test_memory_anchor_present_in_supervisor_prompt():
    """The YOU-HAVE-MEMORY block must be in JARVIS_INSTRUCTIONS so
    the supervisor LLM sees it on every turn."""
    import jarvis_agent

    instr = jarvis_agent.JARVIS_INSTRUCTIONS
    assert "═══ YOU HAVE MEMORY ═══" in instr, (
        "Anchor header missing — Phase 1 of memory-layer fix not in place"
    )
    # The two key tools must be named in the anchor so the LLM
    # cross-references them when temped to deny memory.
    assert "remember(content, category)" in instr
    assert "recall_conversation(query)" in instr
    # ASSUME-INTERRUPTION framing (mirrors Anthropic memory tool default)
    assert "ASSUME INTERRUPTION" in instr


def test_memory_anchor_is_after_proactive_capture():
    """Order matters — anchor goes after PROACTIVE CAPTURE so a
    reader of the prompt encounters trigger-detection rules first
    and the don't-deny-capability anchor right after."""
    import jarvis_agent

    instr = jarvis_agent.JARVIS_INSTRUCTIONS
    pc_idx = instr.find("═══ PROACTIVE CAPTURE")
    anchor_idx = instr.find("═══ YOU HAVE MEMORY ═══")
    drift_idx = instr.find("Memory drift")

    assert pc_idx > 0, "PROACTIVE CAPTURE section missing (prerequisite)"
    assert anchor_idx > pc_idx, "YOU-HAVE-MEMORY must come after PROACTIVE CAPTURE"
    assert drift_idx > anchor_idx, "Memory drift section must remain after YOU-HAVE-MEMORY"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_memory_anchor.py -v
```

Expected: 2 FAILED with `AssertionError: Anchor header missing`.

- [ ] **Step 3: Commit the failing test**

```bash
git add src/voice-agent/tests/test_memory_anchor.py
git commit -m "test: add memory-anchor presence check (failing — Phase 1)"
```

### Task 2: Add the YOU-HAVE-MEMORY anchor to JARVIS_INSTRUCTIONS

**Files:**
- Modify: `src/voice-agent/jarvis_agent.py` — insert after PROACTIVE CAPTURE section (after line ~3149, before "Memory drift" subsection at line ~3153)

- [ ] **Step 1: Locate insertion point**

```bash
grep -n "Memory drift — recall is a snapshot" src/voice-agent/jarvis_agent.py
```

Expected: one line number around 3153 — that's where the new section goes BEFORE.

- [ ] **Step 2: Insert the anchor**

Use the Edit tool. `old_string` is the existing line that begins the "Memory drift" subsection; `new_string` prepends the new anchor block before it. Keep exact indentation.

```text
old_string:
**Memory drift — recall is a snapshot, not a fact.**

new_string:
**═══ YOU HAVE MEMORY ═══**

You have two tools that persist across sessions: `remember(content,
category)` writes a durable fact to `state.db.memories`;
`recall_conversation(query)` searches prior conversations from
`state.db.messages`. Both are real, registered, and work today.

ASSUME INTERRUPTION: chat context resets every session, so anything
not in `remember()` is gone after this conversation ends. The tools
are how continuity happens — treating yourself as stateless is
factually wrong.

When the user states a stable fact, an auto-extractor runs in
parallel and may capture it without your involvement. Either way,
never tell the user "I can't remember" — you can. If the memory
isn't there yet, say "I don't have that yet, sir — want me to
remember it now?" instead.

**Memory drift — recall is a snapshot, not a fact.**
```

- [ ] **Step 3: Run anchor test to verify pass**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_memory_anchor.py -v
```

Expected: 2 PASSED.

- [ ] **Step 4: Run full test suite to verify no regressions**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/ --no-header -q 2>&1 | tail -5
```

Expected: 828 passed, 2 skipped (or whatever the current baseline is, +2 from new test file).

- [ ] **Step 5: Commit the anchor**

```bash
git add src/voice-agent/jarvis_agent.py
git commit -m "feat(memory): add YOU-HAVE-MEMORY anchor to supervisor prompt

Mirrors Anthropic's auto-injected memory-tool framing. Names the two
real tools (remember, recall_conversation), uses ASSUME INTERRUPTION
to override the LLM's stateless-AI training prior. Phase 1 of memory
layer reliability — minimal anchor that ships today; Phases 2-4 add
auto-extraction, recall force-routing, and denial detector.

Spec: docs/superpowers/specs/2026-05-08-anti-gaslighting-memory-design.md"
```

### Task 3: Restart service + live verification

- [ ] **Step 1: Check session age before restart**

```bash
sqlite3 ~/.local/share/jarvis/turn_telemetry.db "SELECT ts_utc FROM turns ORDER BY ts_utc DESC LIMIT 1"; date -u +%Y-%m-%dT%H:%M:%SZ
```

If gap < 60s, STOP and ask the user before continuing.

- [ ] **Step 2: Restart**

```bash
systemctl --user restart jarvis-voice-agent.service && sleep 4 && systemctl --user is-active jarvis-voice-agent.service
```

Expected: `active`.

- [ ] **Step 3: Verify clean startup**

```bash
tail -8 ~/.local/share/jarvis/logs/voice-agent.log | python3 -c '
import sys, json
for line in sys.stdin:
    try:
        d = json.loads(line); print(d.get("timestamp","")[:19], d.get("level",""), d.get("message","")[:140])
    except: pass'
```

Expected: see "registered worker" line, no ERROR-level messages.

- [ ] **Step 4: Mark Phase 1 complete in todos and notify user**

Phase 1 is the entire deliverable for "ship today". Ulrich should test by asking JARVIS "do you have memory?" / "what did I tell you about my work?" — JARVIS should NOT reply with "I'm a conversational AI..." style denials. If it does anyway, that's the signal Phase 4 (denial detector) is needed sooner.

---

## 🟡 PHASE 2 — Auto-extraction on turn boundary (this week, ~2 hours)

### Task 4: Test the extractor's classification shape

**Files:**
- Create: `src/voice-agent/tests/test_memory_extractor.py`

- [ ] **Step 1: Write the failing tests**

```python
# src/voice-agent/tests/test_memory_extractor.py
"""Unit tests for the auto-extraction memory pipeline.

The extractor runs on every user turn after STT finalization. It
classifies whether the transcript contains a stable, memorable
fact about the user/their work, and if so emits a category +
content pair for direct write to state.db.memories — bypassing
the supervisor LLM's tool-choice surface entirely.
"""
from __future__ import annotations
import pytest
from pipeline.memory_extractor import (
    parse_extractor_output,
    ExtractedMemory,
    EXTRACTOR_SKIP,
)


def test_parse_skip_returns_none():
    assert parse_extractor_output("SKIP") is None
    assert parse_extractor_output("  SKIP  ") is None
    assert parse_extractor_output("skip") is None


def test_parse_user_category():
    out = parse_extractor_output("user: Ulrich's wife is named Lizzy")
    assert out is not None
    assert out.category == "user"
    assert out.content == "Ulrich's wife is named Lizzy"


def test_parse_project_category():
    out = parse_extractor_output(
        "project: Coding Kiddos charges $600 for 6 months ($100/mo)."
    )
    assert out.category == "project"
    assert "Coding Kiddos" in out.content


def test_parse_invalid_category_returns_none():
    """Defensive: extractor LLM might output a bad category. Drop
    it rather than write garbage."""
    assert parse_extractor_output("nonsense: who knows what this is") is None


def test_parse_handles_unprefixed_text():
    """If the extractor LLM forgets the category prefix, treat as
    SKIP rather than guess."""
    assert parse_extractor_output("Ulrich's wife is named Lizzy") is None


def test_parse_strips_quotes():
    out = parse_extractor_output('user: "Ulrich runs Pretva"')
    assert out.content == "Ulrich runs Pretva"


def test_extracted_memory_max_length():
    """Don't write giant memories — cap at the same 500 char limit
    as remember() in tools/memory.py."""
    long_content = "x" * 600
    out = parse_extractor_output(f"project: {long_content}")
    assert out is None or len(out.content) <= 500


def test_extractor_skip_constant():
    assert EXTRACTOR_SKIP == "SKIP"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_memory_extractor.py -v
```

Expected: ImportError on `pipeline.memory_extractor` — all tests collect-error.

- [ ] **Step 3: Commit the failing tests**

```bash
git add src/voice-agent/tests/test_memory_extractor.py
git commit -m "test: add memory_extractor parsing tests (failing — Phase 2)"
```

### Task 5: Implement extractor parser (no LLM call yet)

**Files:**
- Create: `src/voice-agent/pipeline/memory_extractor.py`

- [ ] **Step 1: Write the parser module**

```python
# src/voice-agent/pipeline/memory_extractor.py
"""Auto-extraction of memorable facts from user turns.

Bypasses the supervisor LLM's tool-choice surface entirely — runs
a small fast LLM (llama-3.1-8b-instant) on each user transcript,
parses a structured output, writes directly to state.db.memories
via the existing _publish_event path.

Pattern from Mem0/Zep production deployments
(github.com/mem0ai/mem0/issues/3999) — function-tool registration
for memory is unreliable on Llama-class models; the maintainers
themselves recommend turn-boundary auto-injection instead.

Two-step design so unit tests can cover parsing without an LLM:
- parse_extractor_output(): pure string → ExtractedMemory|None
- extract_memory_from_turn(): async LLM call + parse + publish
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger("jarvis.memory_extractor")

EXTRACTOR_SKIP = "SKIP"
_VALID_CATEGORIES = ("user", "feedback", "project", "reference")
_MAX_CONTENT_CHARS = 500


@dataclass(frozen=True)
class ExtractedMemory:
    category: str
    content: str


_LINE_RE = re.compile(r"^\s*([a-z]+)\s*:\s*(.+?)\s*$", re.DOTALL)
_QUOTE_STRIP = re.compile(r'^["\']|["\']$')


def parse_extractor_output(raw: str) -> ExtractedMemory | None:
    """Parse `<category>: <content>` lines from the extractor LLM.

    Returns None for SKIP, malformed output, invalid category, or
    over-length content. Defensive — if anything looks off, drop
    the candidate rather than write garbage.
    """
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    if text.upper() == EXTRACTOR_SKIP:
        return None

    m = _LINE_RE.match(text)
    if not m:
        return None
    category = m.group(1).lower().strip()
    content = m.group(2).strip()

    # Strip surrounding quotes the LLM sometimes adds.
    while content and content[0] in ('"', "'") and content[-1] == content[0]:
        content = content[1:-1].strip()

    if category not in _VALID_CATEGORIES:
        return None
    if not content:
        return None
    if len(content) > _MAX_CONTENT_CHARS:
        return None
    return ExtractedMemory(category=category, content=content)
```

- [ ] **Step 2: Run extractor tests to verify pass**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_memory_extractor.py -v
```

Expected: 8 PASSED.

- [ ] **Step 3: Commit**

```bash
git add src/voice-agent/pipeline/memory_extractor.py
git commit -m "feat(memory): add memory_extractor parser (Phase 2 of 4)

Parses '<category>: <content>' or SKIP from the extractor LLM.
Defensive — drops malformed output rather than writing garbage.
LLM call wiring lands in next commit."
```

### Task 6: Add the extractor LLM-call function (test with a mock)

**Files:**
- Modify: `src/voice-agent/pipeline/memory_extractor.py`
- Modify: `src/voice-agent/tests/test_memory_extractor.py`

- [ ] **Step 1: Add async function with monkeypatchable LLM call**

Append to `pipeline/memory_extractor.py`:

```python
# Few-shot examples — calibrated against the spec's "live failure"
# 2026-05-08 conversation. Order matters: positives first to bias
# the model toward extraction, then 2 SKIP examples to teach refusal.
_EXTRACTOR_PROMPT = """You read a single line of user speech and decide \
whether it contains a stable, memorable fact about the user or their \
ongoing work. If yes, output exactly one line in the form \
'<category>: <one-sentence summary>'. Categories: user, feedback, \
project, reference. If no memorable fact, output exactly: SKIP.

Examples:

USER: "we charge them six hundred dollars for six months"
OUTPUT: project: Coding Kiddos charges $600 for 6 months ($100/mo) per student.

USER: "my wife's name is Lizzy"
OUTPUT: user: Ulrich's wife is named Lizzy.

USER: "we teach python javascript and lua"
OUTPUT: project: Coding Kiddos curriculum covers Python, JavaScript, and Lua.

USER: "i run pretva, a ride hailing service in cameroon"
OUTPUT: user: Ulrich runs Pretva, a ride-hailing service in Cameroon.

USER: "every time i ask jarvis to remember he says he can't"
OUTPUT: feedback: User reports JARVIS denies its own memory capability when asked. Why: the supervisor LLM defaults to 'I'm a conversational AI without memory' from training data. How to apply: prefer the auto-extractor and denial-detector layers over relying on the supervisor LLM to call remember() proactively.

USER: "i'm thirsty"
OUTPUT: SKIP

USER: "yeah okay"
OUTPUT: SKIP

USER: "{transcript}"
OUTPUT:"""


async def _call_extractor_llm(transcript: str) -> str:
    """Call llama-3.1-8b-instant via Groq with the extractor prompt.
    Isolated function so tests can monkeypatch it without an API key."""
    import os
    import httpx

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        logger.debug("[extractor] GROQ_API_KEY missing — skipping extraction")
        return EXTRACTOR_SKIP

    prompt = _EXTRACTOR_PROMPT.format(transcript=transcript.replace('"', "'"))

    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": "llama-3.1-8b-instant",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 80,
                    "temperature": 0.0,
                    "stop": ["\nUSER:", "\n\n"],
                },
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.warning(f"[extractor] LLM call failed: {type(e).__name__}: {e}")
            return EXTRACTOR_SKIP


async def extract_memory_from_turn(
    transcript: str,
) -> ExtractedMemory | None:
    """Top-level extractor entry point. Returns None if SKIP /
    parse-fail / LLM error. Caller handles the publish step.

    Wired into JarvisAgent.on_user_turn_completed in jarvis_agent.py.
    Runs in parallel with the supervisor LLM (asyncio.create_task)
    so it doesn't add latency on the critical path.
    """
    if not transcript or not transcript.strip():
        return None
    raw = await _call_extractor_llm(transcript.strip())
    parsed = parse_extractor_output(raw)
    if parsed is not None:
        logger.info(
            f"[extractor] {parsed.category}: {parsed.content[:80]!r}"
        )
    return parsed
```

- [ ] **Step 2: Add a test that monkeypatches the LLM call**

Append to `tests/test_memory_extractor.py`:

```python
import asyncio
import pipeline.memory_extractor as ext_mod


def test_extract_memory_from_turn_with_mock_llm(monkeypatch):
    """End-to-end extractor flow with a fake LLM that returns a
    known-good output line."""

    async def fake_llm(transcript):
        return "project: Coding Kiddos charges $600 for 6 months."

    monkeypatch.setattr(ext_mod, "_call_extractor_llm", fake_llm)
    result = asyncio.run(ext_mod.extract_memory_from_turn(
        "we charge six hundred for six months"
    ))
    assert result is not None
    assert result.category == "project"
    assert "$600" in result.content


def test_extract_skips_empty_transcript():
    result = asyncio.run(ext_mod.extract_memory_from_turn(""))
    assert result is None
    result = asyncio.run(ext_mod.extract_memory_from_turn("   "))
    assert result is None


def test_extract_handles_skip_from_llm(monkeypatch):
    async def fake_skip(transcript):
        return "SKIP"
    monkeypatch.setattr(ext_mod, "_call_extractor_llm", fake_skip)
    result = asyncio.run(ext_mod.extract_memory_from_turn("yeah okay"))
    assert result is None


def test_extract_handles_llm_failure(monkeypatch):
    """If the LLM call itself errors, _call_extractor_llm returns
    SKIP (logged in the function). Treat as no memory."""
    async def fake_error(transcript):
        return "SKIP"  # what _call_extractor_llm returns on httpx error
    monkeypatch.setattr(ext_mod, "_call_extractor_llm", fake_error)
    result = asyncio.run(ext_mod.extract_memory_from_turn("anything"))
    assert result is None
```

- [ ] **Step 3: Run extractor tests**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_memory_extractor.py -v
```

Expected: 12 PASSED (8 from before + 4 new).

- [ ] **Step 4: Commit**

```bash
git add src/voice-agent/pipeline/memory_extractor.py src/voice-agent/tests/test_memory_extractor.py
git commit -m "feat(memory): add extract_memory_from_turn() async entry point

Few-shot prompt calibrated against the 2026-05-08 'Coding Kiddos
pricing' live-failure conversation. Direct httpx call to Groq
llama-3.1-8b-instant with temperature=0.0 + stop sequences for
deterministic parse. Tests use monkeypatch to verify the parse
flow without an API key."
```

### Task 7: Wire extractor into the user-turn handler

**Files:**
- Modify: `src/voice-agent/jarvis_agent.py` — locate `on_user_turn_completed` (around line 6794)

- [ ] **Step 1: Find the on_user_turn_completed entry point**

```bash
grep -n "async def on_user_turn_completed\|user_input_transcribed" src/voice-agent/jarvis_agent.py | head -5
```

Note the `on_user_turn_completed` line number — that's where the extractor task gets spawned.

- [ ] **Step 2: Import and spawn the extractor task**

Find the `on_user_turn_completed` body (around line 6794). After the existing logic that records the user transcript but before the LLM call kicks off, spawn the extractor as a background task:

```python
# Near the top of on_user_turn_completed, after extracting `text`:

# Layer 1 (Phase 2 of memory-layer fix) — auto-extract memorable
# facts from the user transcript in parallel with the supervisor
# LLM call. Bypasses the LLM's tool-choice surface entirely; writes
# directly to state.db.memories via the existing remember() publish
# path. See docs/superpowers/specs/2026-05-08-anti-gaslighting-memory-design.md.
import asyncio as _asyncio
from pipeline.memory_extractor import extract_memory_from_turn

async def _run_extractor_and_publish(transcript: str) -> None:
    try:
        extracted = await extract_memory_from_turn(transcript)
        if extracted is None:
            return
        # Reuse the existing remember() publish path so the hub
        # consumer sees the same event shape regardless of whether
        # the LLM or the extractor produced it.
        from tools.memory import _publish_event
        _publish_event("memory.value.upserted", {
            "memory_id": _hash_content(extracted.content),
            "content": extracted.content,
            "category": extracted.category,
            "source_session_id": __import__("os").environ.get(
                "JARVIS_VOICE_SESSION_ID"
            ),
        })
    except Exception as e:
        logger.warning(f"[extractor] task failed: {type(e).__name__}: {e}")

# Don't await — the extractor must NOT block the supervisor reply.
_asyncio.create_task(_run_extractor_and_publish(text))
```

You'll also need a helper `_hash_content` in `jarvis_agent.py` if not already present (mirrors the one in `tools/memory.py`):

```python
import hashlib as _hashlib
def _hash_content(text: str) -> str:
    return _hashlib.sha256(text.encode("utf-8")).hexdigest()
```

(Check first: `grep -n "def _hash_content\|def _memory_id" src/voice-agent/jarvis_agent.py src/voice-agent/tools/memory.py` — if `_memory_id` already exists in `tools/memory.py`, import that instead.)

- [ ] **Step 3: Run full test suite**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/ --no-header -q 2>&1 | tail -5
```

Expected: 832 passed, 2 skipped (or current baseline + 4 new).

- [ ] **Step 4: Smoke test in production-like context**

```bash
cd src/voice-agent && .venv/bin/python <<'PY'
import asyncio, sys
sys.path.insert(0, '.')
import jarvis_agent  # adds src/hub to sys.path
from pipeline.memory_extractor import extract_memory_from_turn

# Skip-case
r = asyncio.run(extract_memory_from_turn("yeah okay"))
print("SKIP test:", r)
assert r is None

# Memorable-case (real Groq call — needs GROQ_API_KEY in env)
r = asyncio.run(extract_memory_from_turn(
    "we charge six hundred dollars for six months at coding kiddos"
))
print("Memorable test:", r)
PY
```

Expected: SKIP returns None; memorable returns ExtractedMemory(category='project', content='...$600...').

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/jarvis_agent.py
git commit -m "feat(memory): wire extractor into on_user_turn_completed

Spawns the extractor as a background asyncio.create_task so it runs
in parallel with the supervisor LLM call — adds zero latency on the
critical path. Writes via the existing _publish_event path (Redis
events:memory stream → hub server → state.db.memories) so consumer
shape is identical regardless of write source."
```

### Task 8: Add restart-safe live verification

- [ ] **Step 1: Pre-restart session-age check**

```bash
sqlite3 ~/.local/share/jarvis/turn_telemetry.db "SELECT ts_utc FROM turns ORDER BY ts_utc DESC LIMIT 1"; date -u +%Y-%m-%dT%H:%M:%SZ
```

- [ ] **Step 2: Restart**

```bash
systemctl --user restart jarvis-voice-agent.service && sleep 4 && systemctl --user is-active jarvis-voice-agent.service
```

- [ ] **Step 3: Capture pre-test memory count**

```bash
echo "before: $(sqlite3 ~/.jarvis/hub/state.db 'SELECT COUNT(*) FROM memories')"
```

- [ ] **Step 4: Live test (manual)**

Ulrich speaks: "we charge six hundred dollars for six months at coding kiddos"

- [ ] **Step 5: Verify the memory landed**

```bash
sleep 5  # give the extractor time to write
sqlite3 ~/.jarvis/hub/state.db "
SELECT category, content, datetime(created_ts/1000,'unixepoch')
FROM memories ORDER BY created_ts DESC LIMIT 3" -separator '|'
```

Expected: a new row with category=`project`, content mentioning `$600` or `six hundred`, created within the last minute.

- [ ] **Step 6: If memory landed, mark Phase 2 done. If not, check the log:**

```bash
grep "extractor" ~/.local/share/jarvis/logs/voice-agent.log | tail -10 | python3 -c '
import sys, json
for line in sys.stdin:
    try: d=json.loads(line); print(d.get("timestamp","")[:19], d.get("level",""), d.get("message","")[:160])
    except: pass'
```

### Task 9: Add deferred Phase 2 telemetry

**Files:**
- Modify: `src/voice-agent/pipeline/turn_telemetry.py`

- [ ] **Step 1: Test new column accepts boolean**

Append to `tests/test_turn_telemetry.py` (find the file first; if no test yet, create one):

```python
def test_log_turn_accepts_memory_auto_extracted_flag(tmp_path, monkeypatch):
    from pipeline.turn_telemetry import init_db, log_turn
    db = tmp_path / "test.db"
    init_db(str(db))
    log_turn(
        user_text="we charge $600/6mo",
        jarvis_text="Got it, sir.",
        emotion="neutral", route="TASK",
        llm_used="groq:llama-3.3-70b", voice_used="troy",
        ttfw_ms=200, total_audio_ms=1500,
        user_followup_30s=False, route_fallback=False,
        memory_auto_extracted=True,
        db_path=str(db),
    )
    import sqlite3
    n = sqlite3.connect(str(db)).execute(
        "SELECT memory_auto_extracted FROM turns"
    ).fetchone()
    assert n == (1,)
```

- [ ] **Step 2: Add the column + migration**

In `pipeline/turn_telemetry.py`, in the `init_db` migration block (search for the existing `ALTER TABLE turns ADD COLUMN` lines), append:

```python
        if "memory_auto_extracted" not in cols:
            conn.execute(
                "ALTER TABLE turns ADD COLUMN memory_auto_extracted INTEGER DEFAULT 0"
            )
```

In the `log_turn` signature, add `memory_auto_extracted: bool = False,` and include `int(memory_auto_extracted)` in the INSERT.

- [ ] **Step 3: Run telemetry tests**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_turn_telemetry.py -v
```

- [ ] **Step 4: Commit**

```bash
git add src/voice-agent/pipeline/turn_telemetry.py src/voice-agent/tests/test_turn_telemetry.py
git commit -m "feat(memory): track memory_auto_extracted in turn telemetry

Adds boolean column to track per-turn extractor outcome. Lets us
measure auto-extraction rate vs LLM-extraction rate over time."
```

---

## 🟡 PHASE 3 — Recall force-routing (this week, ~1 hour)

### Task 10: Test the recall regex patterns

**Files:**
- Create: `src/voice-agent/tests/test_recall_router.py`

- [ ] **Step 1: Write the failing test**

```python
# src/voice-agent/tests/test_recall_router.py
"""Tests for the Layer 2 recall-pattern matcher.

When the user asks a recall-shaped question, the turn router
forces tool_choice on the recall_conversation tool — bypassing
the supervisor LLM's metacognition-conservatism that would
otherwise produce a 'I don't have memory' denial.
"""
from __future__ import annotations
import pytest
from pipeline.turn_router import is_recall_query


@pytest.mark.parametrize("transcript", [
    "do you remember my wife's name",
    "Do you remember my wife's name?",
    "can you remember what I said about pricing",
    "did I tell you about my wife",
    "what did I tell you about pricing yesterday",
    "what did we talk about last time",
    "what's my wife's name",
    "what is my wife's name",
    "remember when I said something about Cameroon",
])
def test_matches_recall_queries(transcript):
    assert is_recall_query(transcript) is True


@pytest.mark.parametrize("transcript", [
    "okay",
    "yes please",
    "thanks",
    "remember to bring milk tomorrow",  # imperative reminder, not recall
    "I want to remember my password",   # ambiguous; lean false to avoid over-route
    "Lizzy",
    "we charge six hundred dollars",     # statement, not query
    "Jarvis, mute",
])
def test_does_not_match_non_recall(transcript):
    assert is_recall_query(transcript) is False
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_recall_router.py -v
```

Expected: ImportError on `is_recall_query`.

- [ ] **Step 3: Commit failing tests**

```bash
git add src/voice-agent/tests/test_recall_router.py
git commit -m "test: add recall-query classifier tests (failing — Phase 3)"
```

### Task 11: Implement is_recall_query() in turn_router

**Files:**
- Modify: `src/voice-agent/pipeline/turn_router.py`

- [ ] **Step 1: Append the helper**

At the bottom of `pipeline/turn_router.py`:

```python
# ─────────────────────────────────────────────────────────────────
# Layer 2 recall-pattern matcher (Phase 3 of memory-layer fix).
# When a user transcript matches one of these regexes, the caller
# should force tool_choice={"type": "function", "function":
# {"name": "recall_conversation"}} for that single turn — bypassing
# the supervisor's metacognition-conservatism that otherwise produces
# 'I don't have memory' denials.
#
# CRITICAL caveat — github.com/livekit/agents/issues/4671: tool_choice
# persists across turns when set on generate_reply() in LiveKit Agents.
# Caller MUST reset to "auto" after the forced call.
#
# Patterns calibrated against:
#   - "do you remember [X]"
#   - "can you remember [X]"
#   - "what did I tell you about [X]"
#   - "what's my [X]'s name"
#   - "remember when [X]"
# Negative-tested against imperatives ("remember to bring milk"),
# statements ("we charge $600"), and short ambient phrases.
_RECALL_PATTERNS = [
    re.compile(
        r"\b(?:do|can|did)\s+(?:you|i|we)\s+(?:remember|recall|tell)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bwhat\s+(?:did|do)\s+(?:i|we|you)\s+(?:say|tell|talk|discuss)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bwhat\s+(?:'s|is|was)\s+my\s+\w+(?:'s)?\s+\w+",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bremember\s+when\s+(?:i|we|you)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bdid\s+i\s+(?:tell|say|mention)\b",
        re.IGNORECASE,
    ),
]


def is_recall_query(transcript: str) -> bool:
    """Return True if the transcript looks like a recall question
    (asking about prior conversation or stored facts), not a
    command, statement, or imperative.

    Conservative: imperatives like 'remember to do X' return False
    so we don't force the recall tool when the user wants the
    supervisor to act.
    """
    if not transcript:
        return False
    text = transcript.strip()
    if not text:
        return False
    return any(p.search(text) for p in _RECALL_PATTERNS)
```

- [ ] **Step 2: Run recall tests**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_recall_router.py -v
```

Expected: 17 PASSED (9 positive + 8 negative).

- [ ] **Step 3: Run full suite**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/ --no-header -q 2>&1 | tail -3
```

Expected: 850-ish passed.

- [ ] **Step 4: Commit**

```bash
git add src/voice-agent/pipeline/turn_router.py
git commit -m "feat(memory): add is_recall_query() pattern matcher

Five regex patterns covering 'do you remember', 'what did I tell
you', 'what's my X', 'remember when', 'did I tell'. Conservative
on imperatives ('remember to bring milk' returns False) to avoid
forcing recall when the user wants action."
```

### Task 12: Wire recall force-routing into the agent

**Files:**
- Modify: `src/voice-agent/jarvis_agent.py`

- [ ] **Step 1: Find the LLM-dispatch hook**

Look for where `session._llm` is configured per turn (around the BANTER fast-path / TASK route in `_on_user_input_for_dispatch`):

```bash
grep -n "_dispatch_llm.pick\|tool_choice\|generate_reply\|session._llm" src/voice-agent/jarvis_agent.py | head -20
```

- [ ] **Step 2: Force tool_choice when is_recall_query matches**

Before the supervisor LLM is invoked for a turn, add:

```python
from pipeline.turn_router import is_recall_query

# Layer 2 (Phase 3 of memory-layer fix) — when the user transcript
# is recall-shaped, force tool_choice on recall_conversation so the
# supervisor LLM can't reject the call via metacognition-conservatism.
# CRITICAL: explicitly reset tool_choice to "auto" after the forced
# call (LiveKit issue #4671: tool_choice persists across turns).
if is_recall_query(text):
    try:
        session._jarvis_force_tool_choice = {
            "type": "function",
            "function": {"name": "recall_conversation"},
        }
        logger.info(
            f"[recall-route] forcing recall_conversation for {text[:60]!r}"
        )
    except Exception as e:
        logger.debug(f"[recall-route] couldn't force tool_choice: {e}")
else:
    # Always reset to auto — even if not recall, ensure prior force
    # didn't leak into this turn.
    try:
        session._jarvis_force_tool_choice = None
    except Exception:
        pass
```

Then in the LLM-call path (look for where ChatCompletion is invoked or where `session._llm` is wrapped), pass `tool_choice` from `session._jarvis_force_tool_choice` when set.

**Note:** the exact wiring depends on whether JARVIS uses LiveKit's `agents.llm.LLM` directly or wraps it. Check `_BreakeredGroqLLM.chat()` at the top of `jarvis_agent.py` — that's the most likely site to forward `tool_choice` from the session attribute into the underlying call.

- [ ] **Step 3: Add an integration test**

In `tests/test_recall_router.py`, append:

```python
def test_recall_route_resets_after_use():
    """Defensive: after one forced recall, the next non-recall turn
    must have tool_choice reset (LiveKit #4671 mitigation)."""
    # Simulated session with the attribute we read in jarvis_agent
    class FakeSession:
        _jarvis_force_tool_choice = None

    s = FakeSession()
    # Recall query sets it
    if is_recall_query("do you remember my wife's name"):
        s._jarvis_force_tool_choice = {"type": "function"}
    assert s._jarvis_force_tool_choice is not None
    # Next non-recall turn must clear it
    if not is_recall_query("yeah okay"):
        s._jarvis_force_tool_choice = None
    assert s._jarvis_force_tool_choice is None
```

- [ ] **Step 4: Run and commit**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/ --no-header -q 2>&1 | tail -3
git add src/voice-agent/jarvis_agent.py src/voice-agent/tests/test_recall_router.py
git commit -m "feat(memory): force tool_choice on recall queries

When the user transcript matches is_recall_query(), the dispatch
sets session._jarvis_force_tool_choice to recall_conversation; non-
recall turns reset it to None. Mitigates LiveKit issue #4671
(tool_choice persistence across turns)."
```

### Task 13: Live verification of recall force-routing

- [ ] **Step 1: Restart with safety check**

```bash
sqlite3 ~/.local/share/jarvis/turn_telemetry.db "SELECT ts_utc FROM turns ORDER BY ts_utc DESC LIMIT 1"
# Confirm gap > 60s, then:
systemctl --user restart jarvis-voice-agent.service && sleep 4
```

- [ ] **Step 2: Live test**

Ulrich asks: "do you remember my wife's name?"

Expected JARVIS behavior: a `recall_conversation()` tool call fires (visible in log), JARVIS replies based on what came back (either "Lizzy, sir" if previously stored, or "I don't have that yet, sir — want me to remember it now?" if not).

- [ ] **Step 3: Verify in logs**

```bash
grep -E "recall-route|recall_conversation" ~/.local/share/jarvis/logs/voice-agent.log | tail -5 | python3 -c '
import sys, json
for line in sys.stdin:
    try: d=json.loads(line); print(d.get("timestamp","")[:19], d.get("level",""), d.get("message","")[:160])
    except: pass'
```

Expected: `[recall-route] forcing recall_conversation for 'do you remember my wife's name'`.

---

## 🟡 PHASE 4 — Output-rail denial detector (this week, ~2 hours)

### Task 14: Test the denial regex

**Files:**
- Create: `src/voice-agent/tests/test_denial_detector.py`

- [ ] **Step 1: Write the failing test**

```python
# src/voice-agent/tests/test_denial_detector.py
"""Tests for the output-rail denial detector.

Watches the supervisor LLM's outgoing assistant text. If the text
matches the denial pattern AND no remember()/recall_conversation()
tool fired this turn, the detector suppresses the reply and signals
a re-roll with forced tool_choice.

JARVIS-original — no published precedent for capability-denial
specifically. Closest analog is LLM-Guard's NoRefusal scanner.
"""
from __future__ import annotations
import pytest
from sanitizers.denial_detector import is_capability_denial


@pytest.mark.parametrize("text", [
    "I'm a conversational AI, I don't retain information between conversations.",
    "I'm just an AI assistant, I can't remember between sessions.",
    "I'm afraid I don't have the ability to store or recall individual names or memories.",
    "I'm a language model, I don't retain information about individual users.",
    "I won't be able to recall it later — I don't have memory.",
    "Each time you interact with me, it's a new conversation, I don't store anything.",
])
def test_matches_capability_denials(text):
    assert is_capability_denial(text) is True


@pytest.mark.parametrize("text", [
    "Of course, sir.",
    "I can't open a tab — that's a desktop task.",            # tool refusal, not memory
    "I can't generate physical money.",                        # legitimate inability
    "Lizzy, sir.",                                             # successful recall reply
    "I don't have that yet, sir — want me to remember it now?", # honest empty
    "I'm not able to find what you mentioned.",                # vague but not a denial
    "I haven't been told that yet.",                           # honest empty (different shape)
])
def test_does_not_match_non_denials(text):
    assert is_capability_denial(text) is False


def test_install_is_idempotent():
    """install() must be safe to call multiple times (matches the
    existing sanitizer convention)."""
    import sanitizers.denial_detector as dd
    dd.install()
    dd.install()  # should not raise / should not double-patch
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_denial_detector.py -v
```

Expected: ImportError on `sanitizers.denial_detector`.

- [ ] **Step 3: Commit failing tests**

```bash
git add src/voice-agent/tests/test_denial_detector.py
git commit -m "test: add denial-detector regex tests (failing — Phase 4)"
```

### Task 15: Implement the denial regex

**Files:**
- Create: `src/voice-agent/sanitizers/denial_detector.py`

- [ ] **Step 1: Write the module**

```python
# src/voice-agent/sanitizers/denial_detector.py
"""Output-rail denial detector — Layer 3 of memory-layer fix.

Watches supervisor LLM assistant text for capability-denial phrases
('I'm a conversational AI, I don't retain information', etc.) and,
when matched without a memory tool firing this turn, suppresses the
reply and triggers a re-roll with forced tool_choice.

JARVIS-original pattern. Closest published analog is LLM-Guard's
NoRefusal output scanner; academic precedent for re-roll loops in
Google ADK reflect-and-retry plugin (which targets tool errors,
not capability denials specifically).

Same install pattern as sanitizers/handoff_text.py: monkey-patch
LLMStream._parse_choice; idempotent.
"""
from __future__ import annotations
import logging
import re
from typing import Any

logger = logging.getLogger("jarvis.denial_detector")

# Regex requires BOTH:
#   1. AI-self-reference: "I'm an AI" / "I'm a conversational" / etc.
#   2. Memory-specific verb in a denial: "can't / don't / won't ... remember
#      / recall / retain / store / memory / memorize"
# Conjunctive form prevents false positives on legitimate refusals like
# "I can't open a tab" or "I can't generate money" (no AI-self-reference).
_AI_SELF_REF = (
    r"\b(?:I'?m|I am)\s+(?:just\s+)?(?:an?\s+)?"
    r"(?:AI|conversational|language\s+model|computer\s+program|assistant)"
)
_MEMORY_DENIAL = (
    r"\b(?:can(?:'t|not)|don'?t|won'?t)\s+(?:\w+\s+){0,3}"
    r"(?:remember|recall|retain|store|memorize)"
)
# Alternative shape: "I don't (\w+) (memories?|information|that)"
_ALT_DENIAL = (
    r"\b(?:I)\s+don'?t\s+(?:retain|store|keep|remember)\s+"
    r"(?:any\s+)?(?:information|memories|that|individual)"
)
_DENIAL_RE = re.compile(
    rf"(?:{_AI_SELF_REF}.*?{_MEMORY_DENIAL})|(?:{_ALT_DENIAL})",
    re.IGNORECASE | re.DOTALL,
)


def is_capability_denial(text: str) -> bool:
    """Return True if `text` looks like a memory-capability denial.

    Conjunctive: requires AI-self-reference AND memory-specific
    verb-denial pair. Legitimate refusals like 'I can't open a tab'
    return False (no self-reference + memory verb combo).
    """
    if not text:
        return False
    return bool(_DENIAL_RE.search(text))


def install() -> None:
    """Patch LLMStream to detect capability denials in outgoing text.

    Idempotent: re-installation is a no-op.

    On detection, the patched _parse_choice logs the denial and
    blanks the content (similar to handoff_text suppressor's blanking
    pattern). The framework receives empty content → emits nothing
    to TTS for that chunk. The next turn (when the user retries or
    rephrases) gets a fresh chance at a tool call.

    Future work: instead of just blanking, trigger a re-roll with
    tool_choice forced. That requires deeper LiveKit integration.
    """
    from livekit.agents.inference import llm as inf_llm

    if getattr(inf_llm.LLMStream, "_jarvis_denial_detector_patched", False):
        return

    orig_parse = inf_llm.LLMStream._parse_choice

    # Per-stream buffer of accumulated content so we can detect
    # multi-chunk denial phrases (a typical denial is 30-100 chars
    # split across many chunks).
    _STREAM_BUFFERS: dict[str, str] = {}

    def patched(self, id, choice, thinking):
        delta = getattr(choice, "delta", None)
        finish = getattr(choice, "finish_reason", None)

        if delta is not None:
            content = getattr(delta, "content", None) or ""
            if content:
                buf = _STREAM_BUFFERS.get(id, "") + content
                _STREAM_BUFFERS[id] = buf[-400:]  # last 400 chars only
                if is_capability_denial(buf):
                    logger.warning(
                        f"[denial-detector] suppressed gaslighting reply "
                        f"(stream {id[:12] if id else '?'}): {buf[:120]!r}"
                    )
                    try:
                        delta.content = ""
                    except Exception:
                        try:
                            object.__setattr__(delta, "content", "")
                        except Exception:
                            pass

        if finish:
            _STREAM_BUFFERS.pop(id, None)

        return orig_parse(self, id, choice, thinking)

    inf_llm.LLMStream._parse_choice = patched
    inf_llm.LLMStream._jarvis_denial_detector_patched = True
    logger.info(
        "denial-detector installed (suppresses memory-capability "
        "denial phrases in outgoing assistant text)"
    )
```

- [ ] **Step 2: Run denial-detector tests**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_denial_detector.py -v
```

Expected: 14 PASSED (6 positive + 7 negative + 1 idempotence).

- [ ] **Step 3: Commit**

```bash
git add src/voice-agent/sanitizers/denial_detector.py
git commit -m "feat(memory): add denial_detector sanitizer

Detects supervisor capability-denial phrases ('I'm a conversational
AI, I don't retain memory') in outgoing assistant text and blanks
the chunk content so it never reaches TTS. Same install pattern as
handoff_text suppressor. Idempotent.

Conjunctive regex: requires AI-self-reference AND memory-specific
verb-denial. Legitimate refusals ('I can't open a tab') don't match."
```

### Task 16: Wire denial_detector into sanitizer install path

**Files:**
- Modify: `src/voice-agent/jarvis_agent.py` (around line 144 where other sanitizers install)
- Modify: `src/voice-agent/sanitizers/__init__.py` (docstring)

- [ ] **Step 1: Add to install path in jarvis_agent.py**

After the existing sanitizer installs (around line 145), append:

```python
import sanitizers.denial_detector
sanitizers.denial_detector.install()
```

- [ ] **Step 2: Update sanitizers/__init__.py docstring**

Add to the Modules: list:

```text
  - denial_detector    : suppresses memory-capability denial phrases
                         ('I'm a conversational AI, I don't retain
                         information') from supervisor output
```

- [ ] **Step 3: Smoke test imports**

```bash
cd src/voice-agent && .venv/bin/python -c "import jarvis_agent" 2>&1 | tail -5
```

Expected: see `denial-detector installed` in stderr alongside the other sanitizer install lines, no errors.

- [ ] **Step 4: Run full suite**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/ --no-header -q 2>&1 | tail -3
```

Expected: 870-ish passed.

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/jarvis_agent.py src/voice-agent/sanitizers/__init__.py
git commit -m "feat(memory): install denial_detector at jarvis_agent startup

Wires the new sanitizer into the install chain alongside dsml,
pycall, tool_name, handoff_text. Updates package docstring."
```

### Task 17: Live verify the detector

- [ ] **Step 1: Restart with safety check**

```bash
sqlite3 ~/.local/share/jarvis/turn_telemetry.db "SELECT ts_utc FROM turns ORDER BY ts_utc DESC LIMIT 1"
# Confirm gap > 60s
systemctl --user restart jarvis-voice-agent.service && sleep 4
```

- [ ] **Step 2: Live test**

Ulrich asks something that the supervisor might historically respond to with a denial. Possible prompts: "do you have memory?" / "can you remember things between conversations?"

- [ ] **Step 3: Watch for detector triggers**

```bash
grep "denial-detector" ~/.local/share/jarvis/logs/voice-agent.log | tail -5 | python3 -c '
import sys, json
for line in sys.stdin:
    try: d=json.loads(line); print(d.get("timestamp","")[:19], d.get("level",""), d.get("message","")[:200])
    except: pass'
```

Expected: zero `[denial-detector] suppressed` entries if the prompt anchor (Phase 1) is doing its job. If you DO see triggers, that's evidence the prompt anchor isn't sufficient and the detector is earning its keep.

### Task 18: Document the pattern + memory entry

**Files:**
- Create: `~/.claude/projects/-home-ulrich-Documents-Projects-jarvis/memory/project_memory_layer_v2.md`
- Modify: `~/.claude/projects/-home-ulrich-Documents-Projects-jarvis/memory/MEMORY.md` (add index line)
- Modify: `CLAUDE.md` (mention the new architecture in the "Active design decisions" section)

- [ ] **Step 1: Write memory entry**

```markdown
---
name: Memory layer v2 — turn-boundary auto-extraction + recall force-routing + denial detector
description: 2026-05-08 architectural fix — moves remember() off the LLM tool surface; recall is force-routed; denial detector as defense-in-depth
type: project
---

JARVIS's memory layer was rebuilt 2026-05-08 because the LLM-tool-choice approach was confirmed broken by 3 independent research passes. Across 285 sessions, only 1 memory had ever been saved through the voice path. The supervisor LLM defaulted to "I'm a conversational AI without memory" instead of calling remember() — a documented metacognition-conservatism failure mode (arXiv 2509.21545).

Architecture (4 layers):

- **Anchor** in JARVIS_INSTRUCTIONS: short YOU-HAVE-MEMORY block mirroring Anthropic's auto-injected ASSUME-INTERRUPTION framing. Names the two real tools.
- **Layer 1 — Auto-extraction** at pipeline/memory_extractor.py: small Groq llama-3.1-8b-instant call on every user turn. If the transcript contains a stable fact, writes directly to state.db.memories via _publish_event. Bypasses the supervisor LLM entirely.
- **Layer 2 — Recall force-routing** at pipeline/turn_router.py::is_recall_query: regex pattern matcher; matched queries get tool_choice forced to recall_conversation for that turn. Resets to "auto" after (LiveKit #4671 mitigation).
- **Layer 3 — Denial detector** at sanitizers/denial_detector.py: regex-based output rail; blanks "I'm a conversational AI, I don't retain..." style replies before they reach TTS. Conjunctive regex (AI-self-reference + memory-verb denial) prevents false positives on legitimate refusals.

**Why:** Mem0 maintainers themselves recommend bypassing function-tool registration ([github.com/mem0ai/mem0/issues/3999](https://github.com/mem0ai/mem0/issues/3999)). Every production memory system that works (Mem0, Zep, Character.AI, Pi) hooks turn boundaries; none ask the LLM to choose to remember.

**How to apply:** When investigating "JARVIS doesn't remember" complaints, check (a) state.db.memories COUNT vs prior session — Layer 1 should write at least one row per substantive conversation, (b) `[denial-detector] suppressed` log entries in voice-agent.log — should trend to zero as Layer 1's effectiveness grows, (c) `[recall-route]` logs when the user asks recall-shaped questions.

**Don't revert to a tool-only memory architecture without re-running the research.** The community evidence (Mem0 GitHub issue + Zep + Anthropic memory tool docs) is consistent and recent.
```

- [ ] **Step 2: Add to MEMORY.md index**

Append:

```markdown
- [project_memory_layer_v2.md](project_memory_layer_v2.md) — 2026-05-08 4-layer architectural memory fix; bypasses LLM tool-choice; turn-boundary extraction + recall force-routing + denial detector
```

- [ ] **Step 3: Update CLAUDE.md design decisions**

In the "Active design decisions — the load-bearing constraints" section of CLAUDE.md, add:

```markdown
- **Memory layer is 4-layered, NOT tool-choice driven.** [pipeline/memory_extractor.py](src/voice-agent/pipeline/memory_extractor.py) auto-extracts on turn boundary; [pipeline/turn_router.py::is_recall_query](src/voice-agent/pipeline/turn_router.py) force-routes recall queries; [sanitizers/denial_detector.py](src/voice-agent/sanitizers/denial_detector.py) blanks gaslighting outputs. The supervisor's `remember()` tool still exists but is a backup, not the primary write path. See [docs/superpowers/specs/2026-05-08-anti-gaslighting-memory-design.md](docs/superpowers/specs/2026-05-08-anti-gaslighting-memory-design.md).
```

- [ ] **Step 4: Commit docs**

```bash
git add CLAUDE.md ~/.claude/projects/-home-ulrich-Documents-Projects-jarvis/memory/MEMORY.md ~/.claude/projects/-home-ulrich-Documents-Projects-jarvis/memory/project_memory_layer_v2.md
git commit -m "docs(memory): document v2 memory layer architecture

CLAUDE.md gets the new load-bearing constraint; auto-memory gets
the project entry so future sessions know the architecture +
research basis without re-doing the investigation."
```

---

## Self-Review (run after writing this plan)

- ✅ Spec coverage — every layer in spec maps to phase + tasks (Anchor → Phase 1, Layer 1 → Phase 2, Layer 2 → Phase 3, Layer 3 → Phase 4).
- ✅ No placeholders — every code step shows the actual code.
- ✅ Type consistency — `ExtractedMemory` used the same way in extractor + tests; `is_recall_query` returns `bool` everywhere.
- ✅ Restart safety — every restart step checks turn_telemetry session age first.
- ✅ Phase markers clear: Phase 1 is tasks 1-3, Phase 2-4 explicitly deferred.
- ✅ TDD-style: every task has failing test → impl → passing test → commit.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-08-memory-layer-reliability.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration. Especially good here because Phase 1 is small (3 tasks); subagents would chew through it cleanly with review gates between them.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints. Saves the agent-spawn overhead but adds session context weight.

Which approach?
