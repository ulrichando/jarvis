# Barge-in Truncation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replicate OpenAI Realtime's `audio_end_ms`-based assistant-turn truncation so the LLM's chat_ctx and JARVIS's persisted records both reflect only the heard portion of any interrupted assistant turn — eliminating the "model lies about saying things the user didn't hear" failure mode.

**Architecture:** Three components. (1) A position table on the session — `list[tuple[ms, chars]]` recording cumulative synthesis state. (2) The existing `_LoggingGroqChunkedStream` extended to append one entry per `synthesize()` call. (3) A truncation gate added to the existing `_on_item` (`conversation_item_added`) handler that, when the turn was interrupted, walks the table to find the last fully-played boundary and rewrites both `item.content` (chat_ctx) and the saved text (telemetry + conversations DB).

**Tech Stack:** Python 3.13, livekit-agents, the existing `_LoggingGroqTTS` Groq Orpheus path, pytest in `src/voice-agent/.venv/`.

**Spec:** [docs/superpowers/specs/2026-05-07-barge-in-truncation-design.md](../specs/2026-05-07-barge-in-truncation-design.md)

**Pre-flight (controller-only, before dispatching tasks):**
1. Create a worktree off **master** (not feat/ext-browser-control-v3 — keeps the WIP separate). Branch name: `feat/barge-in-truncation`. Worktree path: `.worktrees/barge-in-truncation` (the `.worktrees` dir already exists and is gitignored).
2. Symlink `src/voice-agent/.venv` from the parent checkout into the worktree (the venv is heavy and reusable read-only).
3. Copy the spec + this plan from the parent checkout's untracked state into the worktree's `docs/superpowers/specs/` and `docs/superpowers/plans/` and commit them on `feat/barge-in-truncation` as the first commit.
4. Run a baseline test (`cd src/voice-agent && .venv/bin/python -m pytest tests/test_turn_router.py -q`) to confirm the venv works in the worktree.

---

## Task 1: Truncation gate helper

**Files:**
- Modify: `src/voice-agent/jarvis_agent.py` (add helper near existing helper functions; aim for a location near `_flatten_chat_content`)
- Create: `src/voice-agent/tests/test_truncation_gate.py`

This task has zero dependency on TTS or LiveKit — it's pure logic over a list of tuples and an `item.content`-shaped object. Write it first.

- [ ] **Step 1: Write the failing tests**

Create `src/voice-agent/tests/test_truncation_gate.py`:

```python
"""Tests for _truncate_to_heard_portion — the barge-in truncation gate
that rewrites an assistant turn to only the heard portion of audio.

Spec: docs/superpowers/specs/2026-05-07-barge-in-truncation-design.md
"""
from __future__ import annotations
from types import SimpleNamespace

from jarvis_agent import _truncate_to_heard_portion


def _make_item(content):
    return SimpleNamespace(content=content)


class TestTruncationGate:
    def test_empty_table_returns_full_text_no_mutation(self):
        item = _make_item("Hello world.")
        text, mutated = _truncate_to_heard_portion(item, [], audio_end_ms=500)
        assert text == "Hello world."
        assert mutated is False
        assert item.content == "Hello world."

    def test_audio_end_ms_zero_returns_empty(self):
        item = _make_item("Hello world.")
        table = [(100, 6), (200, 12)]
        text, mutated = _truncate_to_heard_portion(item, table, audio_end_ms=0)
        assert text == ""
        assert mutated is True
        assert item.content == ""

    def test_cut_at_chunk_boundary(self):
        # Simulates: 2 synth calls, "Hello sir, " (100ms, 11 chars)
        # then "I'm here." (200ms cumulative, 20 chars cumulative).
        # Interrupt at 100ms exactly — heard only first chunk.
        item = _make_item("Hello sir, I'm here.")
        table = [(100, 11), (200, 20)]
        text, mutated = _truncate_to_heard_portion(item, table, audio_end_ms=100)
        assert text == "Hello sir, "
        assert mutated is True
        assert item.content == "Hello sir, "

    def test_cut_mid_second_chunk_falls_back_to_first_boundary(self):
        # User interrupted partway through chunk 2 — we keep only chunk 1
        # (chunk-boundary cut policy from spec).
        item = _make_item("Hello sir, I'm here.")
        table = [(100, 11), (200, 20)]
        text, mutated = _truncate_to_heard_portion(item, table, audio_end_ms=150)
        assert text == "Hello sir, "
        assert mutated is True

    def test_audio_end_ms_past_end_returns_full_no_mutation(self):
        # False/late interrupt — user heard everything.
        item = _make_item("Hello world.")
        table = [(100, 6), (200, 12)]
        text, mutated = _truncate_to_heard_portion(item, table, audio_end_ms=500)
        assert text == "Hello world."
        assert mutated is False
        assert item.content == "Hello world."

    def test_cut_chars_exceeds_text_length_no_mutation(self):
        # Defensive: position table claims more chars than item.content has
        # (could happen if sanitizers shortened text post-synthesis). Don't
        # crash, don't mutate.
        item = _make_item("Hi.")
        table = [(100, 99)]
        text, mutated = _truncate_to_heard_portion(item, table, audio_end_ms=100)
        assert text == "Hi."
        assert mutated is False

    def test_mutation_when_content_is_list(self):
        # livekit-agents wraps content in a list of strings sometimes;
        # the helper must handle both shapes.
        item = _make_item(["Hello world."])
        table = [(100, 5)]
        text, mutated = _truncate_to_heard_portion(item, table, audio_end_ms=100)
        assert text == "Hello"
        assert mutated is True
        assert item.content == ["Hello"]

    def test_none_content_returns_empty_no_mutation(self):
        item = _make_item(None)
        text, mutated = _truncate_to_heard_portion(item, [(100, 5)], audio_end_ms=100)
        assert text == ""
        assert mutated is False
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_truncation_gate.py -v
```
Expected: all 8 tests FAIL with `ImportError: cannot import name '_truncate_to_heard_portion' from 'jarvis_agent'`.

- [ ] **Step 3: Write minimal implementation**

In `src/voice-agent/jarvis_agent.py`, add the following helper. Find an appropriate location — near `_flatten_chat_content` (search for that name) or near other small helper functions. Add right after `_flatten_chat_content`'s definition:

```python
def _truncate_to_heard_portion(item, position_table, audio_end_ms):
    """Cut an assistant turn's text to only the audio that played.

    Used by the barge-in truncation gate in `_on_item`. When the user
    interrupts mid-reply, this returns the heard portion of `item.content`
    and mutates `item.content` in place so chat_ctx for the next turn
    reflects only what was heard. Matches OpenAI Realtime's
    `conversation.item.truncate(audio_end_ms=N)` semantic.

    Spec: docs/superpowers/specs/2026-05-07-barge-in-truncation-design.md

    Args:
        item: livekit-agents chat-ctx item with `.content` (str or [str]).
        position_table: list of (cumulative_ms, cumulative_chars) tuples,
            one entry per synthesize() call in this assistant turn.
        audio_end_ms: ms of audio actually heard (= _jarvis_agent_audio_ms_acc).

    Returns:
        (truncated_text: str, mutated: bool). `mutated` is True iff
        item.content was rewritten to a strictly shorter form.
    """
    full_text = _flatten_chat_content(getattr(item, "content", None)) or ""
    if not position_table:
        return full_text, False

    # Walk to the last entry whose cumulative_ms ≤ audio_end_ms.
    cut_chars = 0
    for cum_ms, cum_chars in position_table:
        if cum_ms <= audio_end_ms:
            cut_chars = cum_chars
        else:
            break

    if cut_chars >= len(full_text):
        # User heard everything (or position table over-reports).
        return full_text, False

    truncated = full_text[:cut_chars]
    # Mutate in place so chat_ctx reflects heard-only on next LLM turn.
    if isinstance(item.content, list):
        item.content = [truncated]
    else:
        item.content = truncated
    return truncated, True
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_truncation_gate.py -v
```
Expected: all 8 tests PASS.

- [ ] **Step 5: Run the full voice-agent suite to confirm no regressions**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/ -x -q
```
Expected: all tests pass (helper is additive, no existing test should break).

- [ ] **Step 6: Commit**

```bash
git add src/voice-agent/jarvis_agent.py src/voice-agent/tests/test_truncation_gate.py
git commit -m "barge-in: add _truncate_to_heard_portion helper with tests"
```

---

## Task 2: Position table reset + recording helper

**Files:**
- Modify: `src/voice-agent/jarvis_agent.py` (add `_record_synthesis` helper, add reset to existing per-turn reset block)
- Create: `src/voice-agent/tests/test_tts_position_table.py`

The recording side is also independent of TTS — it's just list-append math. TDD it before touching the chunk wrapper.

- [ ] **Step 1: Write the failing tests**

Create `src/voice-agent/tests/test_tts_position_table.py`:

```python
"""Tests for _record_synthesis — the helper that appends to the
session's TTS position table after each synthesize() call.

Spec: docs/superpowers/specs/2026-05-07-barge-in-truncation-design.md
"""
from __future__ import annotations
from types import SimpleNamespace

from jarvis_agent import _record_synthesis


def _make_session():
    s = SimpleNamespace()
    s._jarvis_tts_position_table = []
    return s


class TestRecordSynthesis:
    def test_first_call_initializes_running_totals(self):
        sess = _make_session()
        # 11 chars synthesized into 9600 audio bytes
        # 9600 bytes / 96 (bytes/ms) = 100 ms
        _record_synthesis(sess, input_chars=11, audio_bytes=9600)
        assert sess._jarvis_tts_position_table == [(100, 11)]

    def test_second_call_accumulates(self):
        sess = _make_session()
        _record_synthesis(sess, input_chars=11, audio_bytes=9600)
        _record_synthesis(sess, input_chars=9, audio_bytes=19200)  # 200ms
        assert sess._jarvis_tts_position_table == [(100, 11), (300, 20)]

    def test_session_none_is_noop(self):
        # If the wrapper can't find an active session, recording should
        # silently no-op rather than crash the synthesis pipeline.
        _record_synthesis(None, input_chars=10, audio_bytes=1000)
        # No assertion — just verify no exception.

    def test_table_attr_missing_creates_it(self):
        # Defensive: if the session is missing the attr (e.g., first
        # synthesis before reset block ran), create it.
        sess = SimpleNamespace()  # NO _jarvis_tts_position_table
        _record_synthesis(sess, input_chars=5, audio_bytes=480)  # 5ms
        assert sess._jarvis_tts_position_table == [(5, 5)]

    def test_zero_audio_bytes_records_zero_ms_entry(self):
        # Empty/silent synthesis (e.g., letterless input → silent WAV).
        # Still record the input_chars so the next call's running total
        # is correct (or zero so no false truncation).
        sess = _make_session()
        _record_synthesis(sess, input_chars=3, audio_bytes=0)
        assert sess._jarvis_tts_position_table == [(0, 3)]

    def test_zero_input_chars(self):
        sess = _make_session()
        _record_synthesis(sess, input_chars=0, audio_bytes=9600)
        assert sess._jarvis_tts_position_table == [(100, 0)]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_tts_position_table.py -v
```
Expected: all 6 tests FAIL with `ImportError`.

- [ ] **Step 3: Write minimal implementation**

In `src/voice-agent/jarvis_agent.py`, add the recording helper. Locate it directly above `_truncate_to_heard_portion` (added in Task 1):

```python
# Groq Orpheus output is 48 kHz mono 16-bit WAV → 48000 × 1 × 2 = 96 bytes/ms.
# Used by _record_synthesis to convert audio bytes → ms for the position
# table. The 44-byte WAV header rounds to <1 ms — ignored.
_GROQ_ORPHEUS_BYTES_PER_MS = 96


def _record_synthesis(session, input_chars: int, audio_bytes: int) -> None:
    """Append one entry to the session's TTS position table after a
    completed synthesize() call. Idempotent; tolerant of missing session
    or missing attr.

    Spec: docs/superpowers/specs/2026-05-07-barge-in-truncation-design.md
    """
    if session is None:
        return
    table = getattr(session, "_jarvis_tts_position_table", None)
    if table is None:
        table = []
        session._jarvis_tts_position_table = table
    audio_ms = audio_bytes // _GROQ_ORPHEUS_BYTES_PER_MS
    if table:
        prev_ms, prev_chars = table[-1]
    else:
        prev_ms, prev_chars = 0, 0
    table.append((prev_ms + audio_ms, prev_chars + input_chars))
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_tts_position_table.py -v
```
Expected: all 6 tests PASS.

- [ ] **Step 5: Add the per-turn reset**

In `src/voice-agent/jarvis_agent.py`, find the existing per-turn reset block (currently at approximately lines 7567-7569 — search for `_jarvis_agent_audio_ms_acc = 0`). Add two lines immediately after the existing resets:

Current state (around line 7567-7569):
```python
                    session._jarvis_agent_audio_ms_acc = 0
                    session._jarvis_agent_speaking_started_at = None
```

Add two lines so the block becomes:
```python
                    session._jarvis_agent_audio_ms_acc = 0
                    session._jarvis_agent_speaking_started_at = None
                    # Reset TTS position table for the next assistant turn so
                    # interrupt-bookkeeping starts clean. See spec
                    # docs/superpowers/specs/2026-05-07-barge-in-truncation-design.md
                    session._jarvis_tts_position_table = []
```

(One real line plus a 3-line comment — keep the comment to anchor future readers; this is one of the few cases where the WHY isn't obvious from the line itself.)

- [ ] **Step 6: Run the full voice-agent suite**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/ -x -q
```
Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/voice-agent/jarvis_agent.py src/voice-agent/tests/test_tts_position_table.py
git commit -m "barge-in: add _record_synthesis + per-turn position-table reset"
```

---

## Task 3: Wire recording into `_LoggingGroqChunkedStream`

**Files:**
- Modify: `src/voice-agent/jarvis_agent.py` (extend `_LoggingGroqChunkedStream._run` at lines 277-386)

The wrapper extension. Records `audio_bytes` during emission, calls `_record_synthesis` at end. No new test file — this is integration code; covered by the Task 5 E2E test.

- [ ] **Step 1: Read the current `_LoggingGroqChunkedStream._run`**

Open `src/voice-agent/jarvis_agent.py` and read lines 277-386 to see the current `_run` body. Two emission paths exist:
1. The letterless-input short-circuit (lines 287-309) — pushes one silent WAV via `output_emitter.push(_wav)`.
2. The real Groq path (lines 316-386) — streams `data` chunks via `output_emitter.push(data)` in a loop.

Both paths need to record `audio_bytes` and append a position-table entry at the end.

- [ ] **Step 2: Modify `_run` to track audio_bytes and record at end**

Make these specific edits to `_run`:

**(a)** Right after the letterless check on line 287, add tracking variable:

```python
        # Track audio bytes emitted this synthesize() call so we can
        # append a position-table entry for barge-in truncation.
        # Spec: docs/superpowers/specs/2026-05-07-barge-in-truncation-design.md
        synth_audio_bytes = 0
```

**(b)** In the letterless-input path, after `output_emitter.push(_wav)` (around line 307), before `output_emitter.flush()`, change to track the WAV:

```python
            output_emitter.push(_wav)
            synth_audio_bytes += len(_wav)
            output_emitter.flush()
```

Then after `output_emitter.flush()` and before `return`, append:

```python
            # Record this (silent) call in the position table so subsequent
            # synthesize() calls in the same turn see a correct running char
            # total. Audio_bytes counts the silent WAV (~960 bytes ≈ 10ms).
            _sess = _active_session_for_telemetry[0]
            _record_synthesis(_sess, len(self._input_text or ""), synth_audio_bytes)
            return
```

**(c)** In the real Groq path, change the emission loop to track:

Original:
```python
                    async for data, _ in resp.content.iter_chunks():
                        output_emitter.push(data)
                    output_emitter.flush()
```

Replace with:
```python
                    async for data, _ in resp.content.iter_chunks():
                        output_emitter.push(data)
                        synth_audio_bytes += len(data)
                    output_emitter.flush()
```

**(d)** After the entire `_TTS_BREAKER.call(_do_real_run)` block (where the current `_run` exits without explicit return), append the recording call. Find the end of `_run` — it's right after the `await _TTS_BREAKER.call(_do_real_run)` line + its surrounding try/except. Add at the very end of the method (still inside the method, after all the breaker exception handling):

```python
        # Record this synthesize() call in the position table for barge-in
        # truncation. Runs ONLY on success path — if the breaker raised, we
        # don't append (the call's audio wasn't actually heard either).
        _sess = _active_session_for_telemetry[0]
        _record_synthesis(_sess, len(self._input_text or ""), synth_audio_bytes)
```

(The recording call needs to be inside the success branch. If the easiest place is at the end of `_do_real_run` itself, that's fine too — just ensure it's only invoked on success, not after the breaker raises.)

**Important:** verify after editing that the indentation is correct and the recording call is reachable on the success path of BOTH the letterless short-circuit AND the real Groq path. Read the final `_run` end-to-end before committing.

- [ ] **Step 3: Sanity-check the edit visually**

Read `src/voice-agent/jarvis_agent.py` lines 277-410 and confirm:
- `synth_audio_bytes` initialized at the top of `_run`
- Letterless path increments it with `len(_wav)` and calls `_record_synthesis` before `return`
- Real Groq path increments it with `len(data)` per pushed chunk
- Real Groq path calls `_record_synthesis` after successful flush
- Both paths call `_record_synthesis` exactly once per `_run` invocation on success

- [ ] **Step 4: Run the full voice-agent suite**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/ -x -q
```
Expected: all tests pass. The wrapper extension shouldn't break existing TTS tests because `_record_synthesis` is a no-op when `_active_session_for_telemetry[0]` is None (which it is in test contexts that don't set it).

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/jarvis_agent.py
git commit -m "barge-in: wire _LoggingGroqChunkedStream to record TTS position per call"
```

---

## Task 4: Wire truncation gate into `_on_item`

**Files:**
- Modify: `src/voice-agent/jarvis_agent.py` (extend the `_on_item` handler around line 7409)

The gate that consumes the position table. When an interrupt happened, mutate `item.content` and override the text saved to telemetry + conversations DB.

- [ ] **Step 1: Read the current `_on_item` handler**

Open `src/voice-agent/jarvis_agent.py` and read lines 7409-7556 to see the current `_on_item` body. The relevant portion:

```python
    @session.on("conversation_item_added")
    def _on_item(ev) -> None:
        try:
            item = ev.item
            role = getattr(item, "role", None)
            text = _flatten_chat_content(getattr(item, "content", None))
            try:
                prior = list(getattr(session.history, "items", None) or [])
            except Exception:
                prior = []
            _save_turn(convo_session_id, role, text, prior_messages=prior)
            ...
```

The `text` variable is set on line ~7414, then used by `_save_turn` immediately after, and later by `log_turn(jarvis_text=text or "", ...)` at line ~7541. We insert the truncation gate between the `text = ...` line and the `_save_turn(...)` call.

- [ ] **Step 2: Apply the modification**

Find the line `text = _flatten_chat_content(getattr(item, "content", None))` (currently line ~7414). Insert the truncation gate immediately after it and BEFORE the `try: prior = ...` block. The result should look like:

```python
            text = _flatten_chat_content(getattr(item, "content", None))
            # Barge-in truncation gate: if this assistant turn was
            # interrupted, rewrite item.content + the saved text to only
            # the heard portion (OpenAI Realtime parity). Spec:
            # docs/superpowers/specs/2026-05-07-barge-in-truncation-design.md
            if role == "assistant" and getattr(session, "_jarvis_was_interrupted", False):
                audio_end_ms = getattr(session, "_jarvis_agent_audio_ms_acc", 0) or 0
                table = getattr(session, "_jarvis_tts_position_table", None) or []
                original_len = len(text or "")
                truncated, mutated = _truncate_to_heard_portion(item, table, audio_end_ms)
                if mutated:
                    text = truncated
                    logger.info(
                        "[barge-in] truncated assistant turn %d→%d chars at audio_end_ms=%d",
                        original_len, len(text), audio_end_ms,
                    )
            try:
                prior = list(getattr(session.history, "items", None) or [])
            except Exception:
                prior = []
            _save_turn(convo_session_id, role, text, prior_messages=prior)
```

`text` is now possibly truncated. The existing `_save_turn` call uses `text` (correct). The later `log_turn(jarvis_text=text or "", ...)` at line ~7541 also uses `text` (correct — same variable, will reflect the truncation).

**Important:** verify `text` is the variable that flows into BOTH `_save_turn` and `log_turn`. If the original code uses a different variable name in either place, this won't work. Read carefully before editing.

- [ ] **Step 3: Verify by reading the resulting code**

Read lines 7409-7560 end-to-end. Confirm:
- `text` is set once (from `_flatten_chat_content` or its truncation override)
- `_save_turn(...)` uses `text`
- `log_turn(jarvis_text=text or "", ...)` uses `text`
- `item.content` is mutated by the gate (so chat_ctx for the next turn reflects heard-only)

- [ ] **Step 4: Run the full voice-agent suite**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/ -x -q
```
Expected: all tests pass. The gate only activates when `_jarvis_was_interrupted` is True; existing tests don't set that, so behavior is unchanged for them.

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/jarvis_agent.py
git commit -m "barge-in: rewrite assistant turn to heard portion on interrupt"
```

---

## Task 5: E2E pipeline test

**Files:**
- Create: `src/voice-agent/tests/test_barge_in_truncation_e2e.py`

End-to-end test that exercises Tasks 1+2+4 together (Task 3's wrapper integration is harder to fake without a live Groq stream, so we exercise the gate path with a hand-constructed position table).

- [ ] **Step 1: Write the test**

Create `src/voice-agent/tests/test_barge_in_truncation_e2e.py`:

```python
"""End-to-end test for the barge-in truncation flow.

Constructs a fake session + item + position table, simulates the
`conversation_item_added` event firing, and verifies:
- item.content is mutated to the heard portion
- the saved-text variable equals the truncated form
- a non-interrupted turn is left untouched
- empty position table is graceful no-op

Spec: docs/superpowers/specs/2026-05-07-barge-in-truncation-design.md
"""
from __future__ import annotations
from types import SimpleNamespace

from jarvis_agent import _truncate_to_heard_portion


def _build_session(table, audio_end_ms, interrupted):
    return SimpleNamespace(
        _jarvis_tts_position_table=table,
        _jarvis_agent_audio_ms_acc=audio_end_ms,
        _jarvis_was_interrupted=interrupted,
    )


def _simulate_gate(session, item, role):
    """Mirror the inline truncation gate in `_on_item` so the test
    pins the exact integration shape. If the production gate's logic
    changes, this helper must be updated to match — that's the
    coupling we want to catch with this test."""
    from jarvis_agent import _flatten_chat_content
    text = _flatten_chat_content(getattr(item, "content", None))
    if role == "assistant" and getattr(session, "_jarvis_was_interrupted", False):
        audio_end_ms = getattr(session, "_jarvis_agent_audio_ms_acc", 0) or 0
        table = getattr(session, "_jarvis_tts_position_table", None) or []
        truncated, mutated = _truncate_to_heard_portion(item, table, audio_end_ms)
        if mutated:
            text = truncated
    return text


class TestBargeInE2E:
    def test_interrupted_turn_truncated_in_both_item_and_text(self):
        # 3 synthesize calls for "Hello sir, " "I'm here " "to assist."
        # cumulative: (96ms, 11) (192ms, 20) (288ms, 30)
        # Interrupt heard 200ms — last full chunk boundary is 192ms (chunk 2).
        item = SimpleNamespace(content="Hello sir, I'm here to assist.")
        sess = _build_session(
            table=[(96, 11), (192, 20), (288, 30)],
            audio_end_ms=200,
            interrupted=True,
        )
        saved_text = _simulate_gate(sess, item, role="assistant")
        # Both item.content (chat_ctx) and saved_text must equal heard portion.
        assert item.content == "Hello sir, I'm here "
        assert saved_text == "Hello sir, I'm here "

    def test_non_interrupted_turn_left_unchanged(self):
        item = SimpleNamespace(content="Hello sir, I'm here to assist.")
        sess = _build_session(
            table=[(96, 11), (192, 20), (288, 30)],
            audio_end_ms=288,
            interrupted=False,  # no interrupt → gate is a no-op
        )
        saved_text = _simulate_gate(sess, item, role="assistant")
        assert item.content == "Hello sir, I'm here to assist."
        assert saved_text == "Hello sir, I'm here to assist."

    def test_user_role_left_unchanged_even_if_interrupted_flag(self):
        # The interrupted flag applies to assistant turns; user turns
        # must never be truncated.
        item = SimpleNamespace(content="Hello, what's the time?")
        sess = _build_session(
            table=[(96, 6)], audio_end_ms=50, interrupted=True
        )
        saved_text = _simulate_gate(sess, item, role="user")
        assert item.content == "Hello, what's the time?"
        assert saved_text == "Hello, what's the time?"

    def test_empty_position_table_graceful_no_op_even_if_interrupted(self):
        # Dispatcher routed to a TTS without our wrapper → no entries.
        # Gate must not crash; existing text is preserved.
        item = SimpleNamespace(content="Hello.")
        sess = _build_session(table=[], audio_end_ms=100, interrupted=True)
        saved_text = _simulate_gate(sess, item, role="assistant")
        assert item.content == "Hello."
        assert saved_text == "Hello."

    def test_audio_end_ms_zero_records_empty_string(self):
        item = SimpleNamespace(content="Hello sir, I'm here to assist.")
        sess = _build_session(
            table=[(96, 11)], audio_end_ms=0, interrupted=True
        )
        saved_text = _simulate_gate(sess, item, role="assistant")
        assert item.content == ""
        assert saved_text == ""

    def test_late_interrupt_past_total_audio_returns_full(self):
        # Hangover case — user spoke after TTS naturally ended.
        # interrupted=True but audio_end_ms exceeds total_ms.
        item = SimpleNamespace(content="Hello sir, I'm here.")
        sess = _build_session(
            table=[(96, 11), (192, 20)], audio_end_ms=500, interrupted=True
        )
        saved_text = _simulate_gate(sess, item, role="assistant")
        # cut_chars = 20 == len(text), helper returns mutated=False.
        assert item.content == "Hello sir, I'm here."
        assert saved_text == "Hello sir, I'm here."
```

- [ ] **Step 2: Run the test to verify it passes**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_barge_in_truncation_e2e.py -v
```
Expected: all 6 tests PASS (all the production code is already in place from Tasks 1, 2, 4; this test verifies they integrate correctly).

- [ ] **Step 3: Run the full voice-agent suite one more time**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/ -x -q
```
Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add src/voice-agent/tests/test_barge_in_truncation_e2e.py
git commit -m "barge-in: end-to-end test for truncation gate flow"
```

---

## Task 6: Post-deploy verification (manual, runs against live telemetry)

**Files:** none (runs queries against `~/.local/share/jarvis/turn_telemetry.db`)

This task is verification, not implementation. It runs AFTER the worktree merges to master AND the voice-agent service restarts. The goal is to confirm the fix actually works against real interrupts in real conversations.

- [ ] **Step 1: Pre-deploy baseline**

Before merging to master, capture the current bug rate:

```bash
sqlite3 ~/.local/share/jarvis/turn_telemetry.db \
  "SELECT COUNT(*) AS total, SUM(CASE WHEN jarvis_text LIKE '%.' OR jarvis_text LIKE '%?' OR jarvis_text LIKE '%!' THEN 1 ELSE 0 END) AS ends_with_punct FROM turns WHERE interrupted=1 AND ts_utc > datetime('now', '-7 days');"
```

Record the ratio `ends_with_punct / total` — this is the current rate of full-text-saved-despite-interrupt bug, baseline ~30-40%.

- [ ] **Step 2: Merge to master and restart the voice service**

After Task 5 commits, merge `feat/barge-in-truncation` into master (use the same `git push . feat/barge-in-truncation:master` fast-forward pattern from the regression-prevention work):

```bash
cd /home/ulrich/Documents/Projects/jarvis/.worktrees/barge-in-truncation
git push . feat/barge-in-truncation:master
# Then in the parent checkout (when ready, with WIP committed/stashed):
cd /home/ulrich/Documents/Projects/jarvis
# git merge master  # only when feat branch is at a good stopping point
```

For the voice service to pick up the changes, the user must:
1. Have the changes deployed to the parent checkout (either by checking out master or merging master into their working branch)
2. Restart `jarvis-voice-agent.service` per CLAUDE.md operational rule (check turn_telemetry for active session first)

This task documents the steps; the user executes them when ready.

- [ ] **Step 3: Post-deploy live test**

After the service is running with the fix:
1. Have a conversation with JARVIS that involves at least 5 interrupts (start a long reply, barge in mid-sentence).
2. Wait 60 seconds for telemetry to settle.
3. Run the same query as Step 1 against the rows generated AFTER the deploy timestamp.

Expected: `ends_with_punct / total` drops to ≤ 5% for new interrupted rows. The 5% floor accounts for legitimate hangover cases where the reply happened to end at a sentence boundary right when the user spoke.

- [ ] **Step 4: Spot-check item.content via the conversations DB**

Verify the chat_ctx truncation also lands by checking the conversations DB (which receives the same `text` variable):

```bash
sqlite3 ~/.jarvis/conversations.db \
  "SELECT ts, length(content), substr(content, -60) FROM messages WHERE role='assistant' ORDER BY ts DESC LIMIT 10;"
```

Expected: recent assistant messages from interrupted turns end mid-clause (no trailing period, sometimes mid-word at chunk boundary), matching the telemetry truncation.

- [ ] **Step 5: Document the verification result**

Add a one-line entry to the spec at `docs/superpowers/specs/2026-05-07-barge-in-truncation-design.md` under "Verified during spec phase":

```
- ✅ Post-deploy verification YYYY-MM-DD: ends_with_punct ratio dropped from ~X% to Y% on N interrupted rows after fix. Conversations DB also reflects truncated content.
```

```bash
cd /home/ulrich/Documents/Projects/jarvis  # or wherever the spec lives
git add docs/superpowers/specs/2026-05-07-barge-in-truncation-design.md
git commit -m "spec: confirm barge-in truncation fix lands in production"
```

---

## Self-review checklist

- ✅ **Spec coverage:**
  - Component 1 (chunk wrapper) → Task 3
  - Component 2 (position table) → Task 2 (`_record_synthesis` + per-turn reset)
  - Component 3 (truncation gate) → Task 1 (helper) + Task 4 (integration)
  - Edge cases from spec → Task 1 unit tests + Task 5 E2E tests
  - Post-deploy regression query → Task 6
- ✅ **No placeholders:** every step has actual code, actual commands, expected outputs.
- ✅ **Type/name consistency:**
  - `_truncate_to_heard_portion` consistent across Tasks 1, 4, 5.
  - `_record_synthesis` consistent across Tasks 2, 3.
  - `session._jarvis_tts_position_table` consistent across Tasks 2, 3, 4, 5.
  - `_GROQ_ORPHEUS_BYTES_PER_MS = 96` defined in Task 2, used implicitly in Tasks 3 + 5.
  - `_active_session_for_telemetry` referenced consistently (existing module-level holder).
- ✅ **Existing-code references:** lines 277-386 (Task 3), line 7409 (Task 4), lines 7567-7569 (Task 2 reset block) — all actually verified during spec phase by reading the file.
