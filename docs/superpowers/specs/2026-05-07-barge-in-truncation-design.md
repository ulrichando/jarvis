# Barge-in truncation — OpenAI Realtime parity

**Date:** 2026-05-07
**Author:** Ulrich + Claude (brainstorming session)
**Status:** Approved design, ready for implementation plan

## Problem

When a user barges in mid-reply, JARVIS today saves a mix of full-intended-text and heard-portion text to the assistant turn. Telemetry sample (8 most recent `interrupted=1` rows on 2026-05-07/08): 5 are correctly truncated mid-sentence; 3 end with a clean period — almost certainly the full intended text, despite the user only hearing a fraction of it.

This causes the model to "remember saying" things the user never heard. On the next turn it builds on that phantom context, which feels like gaslighting:

- Model says (intended): *"I think the answer is forty-two, but actually let me reconsider — it might be forty-three."*
- User hears: *"I think the answer is forty-tw—"* (interrupts)
- Saved to chat_ctx: full text including "actually let me reconsider — it might be forty-three"
- Next turn, user asks "what was your answer?" — model says "forty-three" because that's what it remembers saying.

OpenAI Realtime solves this with `conversation.item.truncate(audio_end_ms=N)` — the server rewrites the assistant turn to only the audio bytes that played and the corresponding text. The model has no memory of un-heard text.

## Goal

Replicate OpenAI Realtime's `audio_end_ms`-based truncation in JARVIS so that BOTH (a) the saved telemetry/conversation records AND (b) the LLM's chat_ctx for the next turn reflect only the heard portion of any interrupted assistant turn.

## Non-goals

- **Sub-chunk precision.** Truncation cuts at the last fully-played Orpheus chunk boundary, not at character-precision within a chunk. Matches OpenAI's empirical behavior (their `audio_end_ms` is millisecond-precise but the resulting text-truncation is also chunk-bounded by their TTS).
- **Backfill of existing rows.** Historical `interrupted=1` rows in `turn_telemetry.db` and `conversations.db` keep their current (sometimes-full) text. Migration would require re-running TTS, which isn't reasonable.
- **Preserving the un-heard portion separately.** No `jarvis_text_full_intended` audit column. Once truncated, the un-heard portion is gone — same as OpenAI Realtime.
- **Provider-agnostic mechanism.** The wrapper lives in the `_LoggingGroqTTS` layer. Other TTS providers (DeepSeek voice if ever added) would need their own equivalent wrapper. We document this gap in the spec rather than over-engineering for hypothetical providers.

## Architecture

Three components with clear boundaries:

```
┌─ TTS chunk stream wrapper ─┐  ┌─ Position table ──────┐  ┌─ Truncation gate ──┐
│ _LoggingGroqChunkedStream  │─▶│ session._jarvis_tts_  │─▶│ on conversation_   │
│                            │  │ position_table:       │  │ item_added handler │
│ Records (cum_ms, cum_chars)│  │   list[(ms, chars)]   │  │                    │
│ per emitted audio chunk    │  │                       │  │ If interrupted:    │
│                            │  │ Reset on turn-end     │  │  walk table → cut  │
└────────────────────────────┘  └───────────────────────┘  │  pt → mutate item  │
                                                            │  + override saved  │
                                                            │  text              │
                                                            └────────────────────┘
```

**Lifecycle (per turn):**

1. Turn starts → `session._jarvis_tts_position_table = []` (alongside the existing `_jarvis_agent_audio_ms_acc = 0` reset).
2. As Groq Orpheus emits audio chunks via `_LoggingGroqChunkedStream`, the wrapper appends `(running_ms, running_chars)` after each chunk.
3. Either: (a) reply finishes uninterrupted → `_jarvis_was_interrupted` stays False, table is read but ignored; or (b) barge-in fires → `_jarvis_was_interrupted = True` and `_jarvis_agent_audio_ms_acc` records the heard duration.
4. `conversation_item_added` fires (existing handler at [jarvis_agent.py:7409](src/voice-agent/jarvis_agent.py#L7409)). If `_jarvis_was_interrupted` is True, the truncation gate walks the position table for the largest entry where `cumulative_ms ≤ audio_end_ms`. That entry's `cumulative_chars` is the truncation index.
5. Truncate the assistant turn text to that index. Apply to:
   - `item.content` (so chat_ctx for next-turn LLM input sees only heard text → OpenAI-parity model memory)
   - `_save_turn(text=…)` argument (conversations DB)
   - `log_turn(jarvis_text=…)` argument (telemetry DB)
6. Reset table for next turn (alongside the existing per-turn resets at [jarvis_agent.py:7567-7569](src/voice-agent/jarvis_agent.py#L7567-L7569)).

## Component 1 — Chunk wrapper

**Where:** extend `_LoggingGroqTTS` and `_LoggingGroqChunkedStream` at [jarvis_agent.py:432-442](src/voice-agent/jarvis_agent.py#L432-L442).

**Char↔chunk mapping (verified during spec phase).** Groq Orpheus's chunked stream is HTTP byte chunks of WAV audio (see [jarvis_agent.py:367-369](src/voice-agent/jarvis_agent.py#L367-L369)) — `async for data, _ in resp.content.iter_chunks(): output_emitter.push(data)`. The byte chunks carry no text metadata; they're just sample-rate-paced WAV bytes.

**Therefore the meaningful unit is one synthesize() call**, not one HTTP byte chunk. LiveKit's TTS preprocessor segments the LLM's output into utterance-sized texts (typically clauses or short sentences) and calls `_LoggingGroqTTS.synthesize(text=segment)` once per segment. Each call produces one position-table entry.

**Recording per call:**
- At `_run` start: `input_chars = len(self._input_text)`
- During `_run`: accumulate `synthesized_audio_bytes` from each `output_emitter.push(data)`
- At `_run` end: `synthesized_ms = synthesized_audio_bytes / 96` (Orpheus = 48kHz × 1 channel × 16-bit = 96 bytes/ms; the 44-byte WAV header rounds to <1ms — ignore)
- Append to session table: `(prev_running_ms + synthesized_ms, prev_running_chars + input_chars)`

**Why this still satisfies the chunk-boundary cut policy from Section 1:** "chunk boundary" was always referring to the LLM-output→TTS-segmentation boundary, not arbitrary HTTP chunks. Each table entry IS a chunk boundary in the meaningful sense (a complete TTS segment that either fully played or didn't).

**Session access from inside the stream wrapper:** [jarvis_agent.py:7401](src/voice-agent/jarvis_agent.py#L7401) maintains a module-level holder `_active_session_for_telemetry[0] = session` set per session. The wrapper reads from this holder. Cleaner than threading session refs through every `_LoggingGroqTTS(...)` constructor call. JARVIS is single-user; no parallel-session concern.

## Component 2 — Position table

Just session-scoped data:

- `session._jarvis_tts_position_table: list[tuple[int, int]]` — list of `(cumulative_ms, cumulative_chars)` after each chunk.
- `session._jarvis_tts_total_input_chars: int` — set at synthesis start, used by the truncation gate.

Reset point: in the existing per-turn reset block at [jarvis_agent.py:7567-7569](src/voice-agent/jarvis_agent.py#L7567-L7569), add:
```python
session._jarvis_tts_position_table = []
session._jarvis_tts_total_input_chars = 0
```

No locking needed — same single-task write / single-task read pattern as `_jarvis_agent_audio_ms_acc`.

## Component 3 — Truncation gate

**Where:** in `_on_item` at [jarvis_agent.py:7409](src/voice-agent/jarvis_agent.py#L7409), after `text = _flatten_chat_content(...)` and BEFORE `_save_turn(...)`.

```python
def _truncate_to_heard_portion(item, position_table, audio_end_ms):
    """Return (truncated_text, mutated). Mutates item.content in-place if cut."""
    if not position_table:
        return _flatten_chat_content(item.content) or "", False
    cut_chars = 0
    for cum_ms, cum_chars in position_table:
        if cum_ms <= audio_end_ms:
            cut_chars = cum_chars
        else:
            break
    full_text = _flatten_chat_content(item.content) or ""
    if cut_chars >= len(full_text):
        return full_text, False  # heard everything; no cut needed
    truncated = full_text[:cut_chars]
    # Mutate item.content so chat_ctx reflects heard-only on next turn.
    if isinstance(item.content, list):
        item.content = [truncated]
    else:
        item.content = truncated
    return truncated, True
```

**Integration in `_on_item`:**
```python
text = _flatten_chat_content(getattr(item, "content", None)) or ""
if role == "assistant" and getattr(session, "_jarvis_was_interrupted", False):
    audio_end_ms = getattr(session, "_jarvis_agent_audio_ms_acc", 0) or 0
    table = getattr(session, "_jarvis_tts_position_table", None) or []
    original_len = len(text)
    truncated, mutated = _truncate_to_heard_portion(item, table, audio_end_ms)
    if mutated:
        text = truncated
        logger.info(
            "[barge-in] truncated assistant turn %d→%d chars at audio_end_ms=%d",
            original_len, len(text), audio_end_ms,
        )
# Existing flow continues with `text` (now possibly truncated):
_save_turn(convo_session_id, role, text, prior_messages=prior)
# … later, log_turn(jarvis_text=text or "", ...)
```

## Edge cases

| Case | Behavior |
|---|---|
| Empty position table (e.g., dispatcher routed to a non-Groq TTS that doesn't have the wrapper yet) | No truncation. Returns existing `text` unchanged. Documented as graceful degradation pending future provider wrappers. |
| `audio_end_ms = 0` (interrupt before any audio) | `cut_chars = 0` → save empty string. `interrupted=1` records the truth. |
| `audio_end_ms ≥ total_synthesized_ms` (false interrupt / hangover) | Loop iterates through entire table; cut_chars equals total_chars; helper returns `mutated=False` (no real cut). Full text saved. Correct — user heard everything. |
| Reply complete, no interrupt | `_jarvis_was_interrupted` is False; gate not invoked. Existing behavior. |
| Multi-turn replies (supervisor → specialist → back to supervisor) | Each turn-end resets the table. Specialists use their own `_LoggingGroqTTS` instance via dispatcher — same wrapper, same table semantics, separate per-turn resets. |
| Sanitizer-rewritten text | The position table indexes by the text passed INTO `_LoggingGroqTTS.synthesize()`. If a sanitizer rewrites the assistant text after synthesis but before chat_ctx commit, the indexes can drift. Verified during implementation — sanitizers run BEFORE TTS synthesis (input-side) in the current pipeline, so this isn't an issue. |
| Very short replies where chunk 1 itself is partial | Position table has entries where `cumulative_ms` exceeds `audio_end_ms` immediately. `cut_chars` stays 0 → empty string saved. The model knows it was interrupted (`interrupted=1`). Acceptable. |

## Testing

Three test files in `src/voice-agent/tests/`:

### `test_tts_position_table.py` — wrapper unit tests
- Mock `groq.TTS` chunk stream emitting fixed-duration chunks for known input.
- Assert position table has correct `(ms, chars)` tuples for the linear-by-time approximation.
- Cover: 1-chunk reply, multi-chunk reply, empty input, very long input.

### `test_truncation_gate.py` — gate unit tests
- Hand-built position tables + audio_end_ms inputs.
- Assertions for each row in the edge-cases table above.
- Specifically: empty table, audio_end_ms=0, audio_end_ms exactly at chunk boundary, audio_end_ms past end, audio_end_ms mid-chunk.

### `test_barge_in_truncation_e2e.py` — pipeline test
- Stub session with stub `_LoggingGroqTTS` that emits 5 chunks for "Hello sir, I'm here to assist you with anything."
- Fire interrupt at chunk-2 boundary (`audio_end_ms = chunk1_ms + chunk2_ms`).
- Assert:
  - `item.content` was mutated to chunk-2's truncation (e.g., `"Hello sir, "`)
  - Text passed to `_save_turn` matches truncated form
  - Text passed to `log_turn(jarvis_text=…)` matches truncated form
  - `interrupted=1`
  - Position table is reset for next turn

### Post-deploy regression query
Same query that diagnosed the gap becomes the regression check:
```sql
SELECT length(jarvis_text), substr(jarvis_text, -60)
FROM turns WHERE interrupted=1 ORDER BY ts_utc DESC LIMIT 20;
```
Today: ~30-40% end with a period (full text). After deploy: should drop to ≤ 5% (only true hangover-no-real-interrupt cases).

## Files to touch

- [src/voice-agent/jarvis_agent.py](src/voice-agent/jarvis_agent.py)
  - Lines 432-442: extend `_LoggingGroqTTS` / `_LoggingGroqChunkedStream` to record per-chunk position
  - Around line 7409: add truncation gate to `_on_item` handler before `_save_turn`
  - Lines 7567-7569: add reset of position table to existing per-turn reset block
- New: `src/voice-agent/tests/test_tts_position_table.py`
- New: `src/voice-agent/tests/test_truncation_gate.py`
- New: `src/voice-agent/tests/test_barge_in_truncation_e2e.py`

No changes to:
- LiveKit's adaptive interruption — left alone, runs in parallel for audio flush.
- The `interrupt() → clear_buffer() → clear_queue()` chain — untouched.
- Sanitizers, dispatcher, specialists.

## Risks

- **Linear-by-time inaccuracy at chunk boundaries.** If our running approximation drifts, the cut may land 1-2 chars off. Mitigated by chunk-boundary cut policy: error is bounded to within one chunk's worth of chars (~30-50 chars in normal speech). Acceptable; OpenAI Realtime has a similar approximation error.
- **Groq chunk metadata absent.** If `_LoggingGroqChunkedStream`'s chunk objects don't expose `text_offset`/`text_length`, we fall back to the linear approximation. Implementation task verifies which case we're in.
- **Dispatcher-routed turns to non-Groq TTS.** Position table will be empty → graceful no-op. Documented as known gap. To fix later when DeepSeek voice (or others) is wired in.
- **Mutation of `item.content` could break downstream consumers expecting the original.** All known consumers ([handoff_text_suppressor](src/voice-agent/sanitizers/handoff_text.py), [confab_detector](src/voice-agent/confab_detector.py)) read `item.content` AFTER `_on_item` fires, so they see the mutated form — which is the desired OpenAI semantic. Verified during implementation.

## Verified during spec phase

- ✅ `_LoggingGroqTTS` is a thin shim ([jarvis_agent.py:432-442](src/voice-agent/jarvis_agent.py#L432-L442)) — only overrides `synthesize()` for error logging. No existing playback-position bookkeeping to break.
- ✅ Save path is `_on_item` at [jarvis_agent.py:7409](src/voice-agent/jarvis_agent.py#L7409); reads `text = _flatten_chat_content(item.content)`. Mutating `item.content` before this read works.
- ✅ `_jarvis_agent_audio_ms_acc` already tracks heard ms ([jarvis_agent.py:7530-7538](src/voice-agent/jarvis_agent.py#L7530-L7538)) — wired 2026-05-07 for `total_audio_ms` telemetry. Reusable directly as our `audio_end_ms`.
- ✅ Telemetry diagnostic query confirms the gap: 5/8 recent interrupted rows truncated, 3/8 saved full text.

## Out of scope (explicitly)

- Sub-chunk char-level precision (would require Orpheus internal sample mapping — not justified).
- Backfilling historical rows (would require re-synthesizing — not reasonable).
- Preserving un-heard text in a separate audit column (rejected by user — full OpenAI parity = the un-heard text is gone).
- Wrapping non-Groq TTS providers (DeepSeek, Kimi) — done case-by-case as those providers come online.
- Reworking LiveKit's adaptive interruption — runs in parallel, untouched.
