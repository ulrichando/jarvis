# Echo-aware barge-in gate — design

**Date:** 2026-05-20
**Status:** PARTIALLY IMPLEMENTED — foundations landed + tested, interrupt surgery paused (see Implementation status)
**Author:** Ulrich + Claude
**Related:** [[project_barge_in_speakers_blocked]], [[project_aec_echo_stt_regression]], `2026-05-18-barge-in-interrupt-fix-design.md`, `2026-05-19-echo-cancellation-cascade-design.md`

## Implementation status (2026-05-20)

**Landed + unit-tested — behavior-neutral** (nothing consumes the tracker yet; the
client mic still drops during TTS, so JARVIS behaves exactly as the pre-feature
baseline; the keyterm STT fix is unrelated and live):
- `pipeline/echo_gate.py` — `is_echo()` core + `enabled()` kill-switch
  (`JARVIS_ECHO_AWARE_BARGEIN`, default on). `tests/test_echo_gate.py` — 9 green.
- `pipeline/speaking_tracker.py` — process-local speech-text capture. **Refinement
  vs spec:** replaces `session._jarvis_speaking_text` because the Orpheus TTS shim
  has no `AgentSession` handle; one worker job = one session, so process-local ==
  session-scoped. `tests/test_speaking_tracker.py` — 6 green.
- `providers/tts.py` — `speaking_tracker.note_speaking(self._input_text)` in `_run`.
- `jarvis_agent.py` — `speaking_tracker.reset()` per session in
  `_register_state_tracking_handlers`; `mark_speech_ended()` in `_on_agent_state`
  when leaving "speaking".

**Remaining — PAUSED:**
- **Consumer A (interrupts):** neuter the raw VAD-direct handler during TTS **and
  disable the framework's built-in VAD interruption** (`turn_handling.interruption.
  enabled=False` when the feature is on) + add an echo-aware STT-partial interrupt
  handler. *The framework-interruption-disable is a design ADDITION discovered
  during implementation* — without it the framework self-interrupts on echo
  regardless of our handlers (verified: 3 interrupt triggers exist, not 1).
- **Consumer B (phantom turns):** echo-check in `on_user_turn_completed` right
  after the existing garbage gate (`is_echo(text, recent_speaking_text(2.0))` →
  `raise StopResponse()`).
- **Client hot-mic:** publish during TTS when feature on, in `jarvis_voice_client.py`
  `_should_publish_during_speak` + kill-switch. **Do NOT land this without Consumer A**
  — a hot mic with no echo gate reintroduces the self-interrupt/phantom-turn regression.

**Unblock conditions:** (1) the SOUL-prompt refactor is concurrently rewriting
`jarvis_agent.py` (the interrupt-path file) — let it settle to avoid clobbering;
(2) host CPU headroom for a clean suite baseline (currently flaky under load) + a
live barge-in test.

## Problem

Voice barge-in is dead on **speakers**. The client mic-gate (`jarvis_voice_client.py:811-818`) drops every mic frame while JARVIS speaks, because a live mic on speakers feeds JARVIS's own Orpheus output back into STT as echo. With the mic muted during TTS, Nova-3 and the VAD receive silence, so `session.interrupt()` never fires — the user cannot talk over JARVIS.

Turning the mic hot (`JARVIS_MIC_DURING_SPEAK=1`) re-enables barge-in but reintroduces two echo failures, both observed live:
1. **Self-interrupt** — VAD/STT trips on JARVIS's own voice → JARVIS cuts itself off ("stopped replying").
2. **Phantom turns** — echo gets transcribed and finalized as a fake *user request* → JARVIS responds to itself (the original 2026-05-20 AEC regression).

The proper acoustic fix (L3/DTLN neural residual canceller) is unbuilt and heavy. This design gets voice barge-in on speakers **without** DTLN, by discriminating echo from real speech at the **text** level — JARVIS knows the words it is currently speaking, so anything transcribed that merely repeats those words is echo.

## Goals / Non-goals

**Goals**
- Voice barge-in works on speakers: the user can talk over JARVIS and it stops.
- JARVIS does **not** interrupt itself on its own echo.
- Echo never becomes a phantom user turn.
- Default-on for speakers, with a kill-switch.

**Non-goals**
- **Loud-echo masking** — if echo is loud enough that the user's words never surface in the transcript, this design treats it as echo. That is the DTLN case, explicitly out of scope.
- Headphones (already works — no echo path; gate already returns hot-mic=true).
- Changing STT/TTS providers or the SFU interrupt→clear_queue path.

## Approach: STT-partial echo filter (Approach ①)

Keep the mic hot during TTS. Nova-3 streams partial transcripts (~150 ms) as JARVIS speaks. Classify each transcript as **echo** or **novel** relative to JARVIS's concurrent speech; only novel speech drives an interrupt or a user turn. Barge-in latency ~150–300 ms (Nova-3 partial) — under the <500 ms target, and the streaming partials are precisely what Nova-3 was added for.

*Rejected alternatives:* ② provisional-interrupt-then-resume (relies on `resume_false_interruption`, documented broken on this SFU); ③ acoustic/waveform echo gate (= DTLN, the heavy build we're avoiding).

## Architecture — one core function, four feed points

**Core (pure, unit-testable):** new module `pipeline/echo_gate.py`
```
is_echo(transcript: str, speaking_text: str) -> bool
    # 1. Kill-phrase ("stop|wait|hold on|cancel|hush|...") in transcript -> return False
    #    (NEVER echo; always allowed through — the safety net).
    # 2. Tokenize both to lowercase content words (drop stopwords + punctuation).
    # 3. novel = transcript_content_words NOT present in speaking_text content-word set.
    # 4. echo  <=>  len(novel) < MIN_NOVEL_WORDS  (default 2).
```
Kill-phrase regex is reused from the existing `_KILL_PHRASES` in `jarvis_agent.py` (single source).

**Feed points:**

| # | Responsibility | Where |
|---|---|---|
| 1 | **Capture** JARVIS's live speech: append `self._input_text` to `session._jarvis_speaking_text` as Orpheus synthesizes each chunk | `providers/tts.py::LoggingGroqChunkedStream._run` |
| 2 | **Snapshot + reset** on speech end: copy `_jarvis_speaking_text` → `_jarvis_recent_speech_text` + stamp `_jarvis_speech_ended_at`, then reset the live accumulator. (Consumer B needs this — the live buffer is already gone by the time a phantom echo-turn finalizes post-endpointing.) | `jarvis_agent.py::_on_agent_state` (exists) |
| 3 | **Consumer A — interrupts:** VAD-direct (`:4851`) + kill-phrase (`:4835`) handlers consult `is_echo` before `session.interrupt()` while `agent_state=="speaking"`; pure echo → no interrupt | `jarvis_agent.py` |
| 4 | **Consumer B — phantom turns:** drop a finalized user turn that `is_echo` of `_jarvis_recent_speech_text`, when it finalizes within `RECENT_SPEECH_TTL` (default 2 s) of speech end | `on_user_turn_completed` (or `pipeline/stt_gate.py`) |
| — | **Hot mic:** publish mic during TTS when feature on (default) | client `_should_publish_during_speak` / `audio/aec_health.py` |
| — | **Kill-switch:** `JARVIS_ECHO_AWARE_BARGEIN` (default `1`); `0` → revert to mic-drop-during-speak (status quo) | client + agent |

## Data flow

```
JARVIS speaks ─▶ TTS appends text ─▶ session._jarvis_speaking_text
user talks    ─▶ hot mic ─▶ Nova-3 partial ─▶ user_input_transcribed
                                  │
                    agent_state == "speaking"?
                                  │ yes
                          is_echo(partial, speaking_text)?
                          ├─ novel  ─▶ session.interrupt()  (barge-in)
                          └─ echo   ─▶ ignore               (no self-interrupt)
JARVIS stops  ─▶ snapshot speaking_text → recent_speech_text (TTL 2s), reset accumulator
turn finalizes ─▶ is_echo(turn, recent_speech_text)? ─ echo ─▶ drop (no phantom)
```

## Decision parameters (env-overridable, sensible defaults)
- `MIN_NOVEL_WORDS` = 2 — **bias-to-suppress**: a self-interrupt is the worse, more visible failure than occasionally having to say a couple words to barge in.
- `RECENT_SPEECH_TTL` = 2 s — window after speech end during which a finalizing turn is still echo-checked against the just-spoken text (Consumer B).
- Kill-phrases bypass the gate entirely (always interrupt).

## Edge cases
- **Empty `speaking_text`** (no active TTS) → gate inert → normal barge-in behavior.
- **Feature off** (`JARVIS_ECHO_AWARE_BARGEIN=0`) → mic-drop fallback = exact status quo.
- **Short/stopword-only transcript** → < MIN_NOVEL novel words → treated as echo → suppressed.
- **Kill-phrase during echo** → always interrupts (safety net for the loud-echo-masking limitation).

## Testing
- **Unit (the bulk), `tests/test_echo_gate.py`** — pure-function TDD on `is_echo`: echo subset → True; ≥2 novel words → False (interrupt); kill-phrase → False (allow) even with zero novel words; 1 novel word → True (suppress); stopword-only / empty → echo; case + punctuation normalization.
- **Capture/clear** — TTS appends; `_on_agent_state` clears (focused test).
- **Wiring** — interrupt handler + turn-admission consult the gate (light test; integration verified live).

## Limitations (carried forward)
1. **Loud-echo masking** → DTLN, out of scope (kill-phrase path bounds the downside).
2. **Added load** — hot mic ⇒ Nova-3 streams through every reply ⇒ more STT load on a marginal 4-core host; kill-switch is the escape hatch.

## Rollout
Default-on for speakers; `JARVIS_ECHO_AWARE_BARGEIN=0` to disable. Build + unit-test offline (host-independent); live-test on speakers when the host has CPU headroom.

## File-level change list
- **new** `pipeline/echo_gate.py` + `tests/test_echo_gate.py`
- `providers/tts.py` — append `_input_text` to session speaking-text
- `jarvis_agent.py` — clear speaking-text on state change; consult gate in interrupt handlers + turn admission
- `jarvis_voice_client.py` (+ `audio/aec_health.py`) — publish mic during TTS when feature enabled; honor kill-switch
