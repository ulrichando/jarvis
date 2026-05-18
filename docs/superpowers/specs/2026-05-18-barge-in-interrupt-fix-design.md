# Barge-in interrupt: cut JARVIS's perceived stop from 1-3 s to 200-300 ms

**Status:** spec
**Date:** 2026-05-18
**Why now:** User complaint, validated live: when they start talking while JARVIS is speaking, JARVIS keeps speaking for 1-3 s before going silent. Industry frontier (OpenAI Realtime, Vapi, Pipecat) is **200-300 ms perceived stop**. JARVIS is 5-15× worse. Detection IS firing (VAD + `session.interrupt()` runs); the queue-clearing across the audio chain is what's broken.

## Goal

Reduce perceived "JARVIS keeps talking after I interrupt" from 1-3 s to ≤ 300 ms, matching OpenAI Realtime / Vapi class. No regression to false-barge-in (the VAD work landed today already addresses that — `min_speech=0.05`, `activation=0.5`).

## Where the 1-3 s comes from — diagnostic certainty

Sourced from the researcher agent's audit of installed `livekit-agents 1.5.9` and `livekit-rtc 1.1.8`:

| Layer | Buffer size | Cleared by `session.interrupt()` today? | Fixable? |
|---|---|---|---|
| **Orpheus TTS upstream** (Groq stream) | unbounded — keeps `push()`-ing frames | ❌ NO — no cancel signal back to provider | ✅ wrap stream in cancellable task |
| **`AudioSource` internal queue** | 1000 ms (default) | partially — `clear_queue()` known-buggy ([livekit/python-sdks#394](https://github.com/livekit/python-sdks/issues/394)) | ✅ shrink to 200 ms |
| **WebRTC jitter buffer** | 15-120 ms | n/a (subscriber-side) | ❌ leave alone |
| **Opus encoder lookahead** | ~20 ms | n/a | ❌ leave alone |
| **PortAudio output** | 200 ms (`JARVIS_PLAYBACK_LATENCY_S=0.2`) | not affected by `clear_buffer()` | ✅ optional: add subscriber-side drain |

The compounding 1-3 s is: **stale TTS chunks pushed after `clear_buffer()`** (largest, often >1 s) + **1 s AudioSource queue** + ~150 ms of WebRTC/Opus/PortAudio (irreducible).

## Architecture — three layered fixes

```
              ┌─── on session.interrupt() fires ────┐
              │                                      │
       (1) TTS upstream                       (2) AudioSource
           cancel token                          shrink queue
       ─────────────────                       ─────────────────
       Wrap each Orpheus stream                Set queue_size_ms=200
       in asyncio.Task with cancel             on rtc.AudioSource ctor
       attached to interrupt event.            (was 1000 default).
       Cancelling the task stops new           ~800 ms instant savings
       frames from being push()'d into         on the queue layer.
       _audio_bstream.

              ┌─── BOTH apply → 200-300 ms total ─┐
              │
       (3) Subscriber-side drain — DEFERRED to v2 (more complex)
       ─────────────────────────────────────────────────────────
       Agent sends custom data packet "lk.clear_buffer" when
       interrupting; voice-client drains its PortAudio queue
       with a 20 ms fade-out (Pipecat's anti-click pattern).
       Saves the final ~150 ms; not needed for the 300 ms target.
```

## Fix 1 — TTS upstream cancellation (biggest win, ~70-80% of residual)

**Where:** `src/voice-agent/jarvis_agent.py::_LoggingGroqTTS` + `src/voice-agent/pipeline/dispatching_tts.py`.

**Today:** Each synth call returns a stream. The stream's `__anext__` keeps producing frames from Groq's HTTP response. When `session.interrupt()` fires, the framework calls `clear_buffer()` on the audio output, but the still-alive Orpheus stream keeps `push()`-ing fresh frames into the now-empty buffer. So the queue refills.

**Change:**
- Wrap each Orpheus stream's frame-pump loop in an `asyncio.Task`.
- Track the live task on the TTS adapter instance.
- Hook `session.on("agent_state_changed")` (or `audio_output.clear_buffer` event if available) → cancel the live task.
- On cancel, the stream's `__anext__` raises `CancelledError`; the framework's StreamAdapter catches it cleanly. No frames pushed thereafter.

Verification: log `[tts-cancel] stream cancelled after Nms` on each interrupt.

## Fix 2 — Shrink `AudioSource` queue 1000 → 200 ms

**Where:** wherever the agent constructs `rtc.AudioSource(...)`. Grep for `AudioSource(` to locate.

**Change:** add `queue_size_ms=200` to the constructor. Default `1000` ms is for jitter resilience over flaky networks — JARVIS runs on local LiveKit server (`127.0.0.1:7880`); jitter is microseconds. The 800 ms safety is pure latency cost.

Trade-off considered: under network blips, a smaller queue means a small chance of audio underrun (a brief silence). For loopback there is no realistic scenario where this triggers. If we ever move LiveKit off-box, revisit.

## Fix 3 — Confirm adaptive interruption is on

**Where:** wherever the `AgentSession` is constructed.

**Change:** verify `interruption_detection="adaptive"` is set (LiveKit 1.5+ feature, [blog](https://livekit.com/blog/adaptive-interruption-handling)). The adaptive model needs median 216 ms of speech to decide vs raw VAD's 50-200 ms — but adaptive cuts false-barge-in rate by ~64%, which means JARVIS stops on real interrupts and ignores breaths / "uh" / TTS reverb. Net: detection latency similar, false-barge-in dramatically lower.

If not currently set, this is a one-line change.

## Files to touch

| File | Change | Lines (est) |
|---|---|---|
| `src/voice-agent/jarvis_agent.py` | `_LoggingGroqTTS` cancel-token wrap; verify AgentSession `interruption_detection=adaptive` | ~40 add |
| `src/voice-agent/pipeline/dispatching_tts.py` | propagate cancel through the per-route dispatcher | ~20 add |
| `src/voice-agent/providers/tts.py` (or whichever wraps the rtc.AudioSource) | add `queue_size_ms=200` | 1 line |
| `src/voice-agent/tests/test_tts_cancellation.py` | NEW — verify cancel-on-interrupt fires | ~100 lines |
| `CLAUDE.md` | document the queue_size_ms + cancel-token design (load-bearing constraint) | ~5 lines |

## Build sequence

1. **Locate the `AudioSource` constructor**, add `queue_size_ms=200`, restart, measure (immediate 800 ms savings — verify with a "Jarvis, say something long" → barge-in test, eyeball perceived stop)
2. **Verify `interruption_detection="adaptive"`** in the AgentSession config; flip it on if not (low-risk)
3. **`_LoggingGroqTTS` cancel-token wrap** — the bigger lift. Add the task tracker, wire to `clear_buffer` event, test with mocked TTS stream
4. **Live verification** — record a "say something long, interrupt at 2 s" exchange, count milliseconds until silence (target ≤ 300 ms)
5. **(Deferred to v2)** Subscriber-side drain via custom data packet

## Verification plan

- **Unit:** mock the TTS stream to keep producing forever. Fire `clear_buffer`. Assert the stream task transitions to cancelled within 50 ms of the event.
- **Unit:** read back `rtc.AudioSource` instance, assert `_queue_size_ms == 200`.
- **Integration (manual):** "Jarvis, count from one to thirty slowly." At "ten", say "stop". Measure perceived stop time with a stopwatch / phone recording. Should be < 300 ms.
- **Regression:** full voice-agent suite stays green (currently 1612 passed).

## Risks & mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Smaller AudioSource queue causes audio underruns on local loopback | low | Loopback has 0 jitter. If we hear glitches, bump to 300 ms (still 700 ms savings). |
| TTS cancel raises in the wrong spot, crashes the agent | medium | Wrap in try/except; on failure log + fall through to current behavior (no regression vs today). |
| `adaptive` interruption changes false-positive rate in unexpected direction | low | LiveKit 1.5+ has it as recommended default. Kill switch: `interruption_detection="vad"`. |
| Cancel propagation doesn't reach Groq's HTTP stream (httpx connection stays open) | medium | Close the httpx response explicitly on cancel; Groq stops billing per their docs. |
| Custom data-packet drain breaks existing voice-client logic (v2 only) | n/a | Deferred — not in v1. |

## Out of scope

- Subscriber-side drain via custom data packet — deferred to v2. The 300 ms target hits without it.
- Replacing Orpheus TTS — works fine, just needs cancellation.
- Touching the VAD knobs again — landed today (act=0.5 + min_speech=0.05).
- WebRTC jitter buffer tuning — outside the framework's API surface.
- Opus encoder lookahead — outside controllable layer.

## Effort

~3 hours. Sequenced 1→4 with verification after each so we can stop early if intermediate measurements already hit 300 ms.

## Open questions for spec review

1. **Target latency** — is 300 ms acceptable, or do you want me to aim for 200 ms (would require the v2 subscriber-side drain)? **Recommend 300 ms for v1.**
2. **Audio fade-out vs hard cut** — when the cancel fires, audio just stops abruptly (clean but may have a faint "click"). Pipecat's pattern is a 20 ms linear fade to silence. **Recommend hard cut for v1 — simpler, ship faster; add fade in v2 if you hear clicks.**
3. **`adaptive` vs current `vad` interruption detection** — adaptive is more conservative (216 ms median speech needed). If you want JARVIS to react to even a faint "uh", stay on VAD. If you want fewer false interrupts, switch to adaptive. **Recommend adaptive — JARVIS already over-interrupts in our recent logs.**
