# Silent-mode token/privacy leak fix — local-wake mute + honest indicator

Status: IMPLEMENTED 2026-06-18 (Architecture A). Phase 1 live (honest indicator
+ honcho gate, committed 1c16e6de). Phase 2 code-complete + suite-green, gated
behind `JARVIS_SILENT_LOCAL_WAKE=1` (default off).
Branch: master (feature branch merged in; commits local-only).

## Why this is a spec, not shipped code

This touches three load-bearing/sensitive surfaces — the live Deepgram
streaming barge-in path, the FROZEN tray indicator, and the voice-client
mic pump — so the approach and the explicit "do-not-touch" list are
written down before any edit. It also spans two subtrees (voice-agent +
desktop-tauri), which `regression-prevention.md` flags as the routine
break-vector.

## The problem (what the investigation found)

JARVIS has **two independent quiet-states**, and they behave very
differently:

| State | Set by | Mic published to SFU? | Cloud STT / cost |
|---|---|---|---|
| **`muted`** (hard) | Tray **Mute** button → `:8767/mute` | No — pump returns early (`jarvis_voice_client.py:1101`) | None ✅ |
| **`silent_mode`** (soft) | **Voice** "mute / go quiet" → `~/.jarvis/.silent-mode` | **Yes** — pump only checks `state.muted` | **Leaks** 🔴 |

Under `silent_mode` the mic keeps publishing, so the voice-agent keeps
running **Deepgram Nova-3 streaming STT** on every utterance (billed per
minute, and room audio leaves the box to a third party). The reply is
suppressed only *after* STT, at `jarvis_agent.py:4251`
(`raise StopResponse()` in `on_user_turn_completed`), so the user hears
nothing and assumes JARVIS is off — while it is still transcribing
everything. honcho memory sync (`jarvis_agent.py:6561`) also fires per
user message into its OpenAI-backed deriver.

Compounding it: the tray indicator is **dishonest**. Its black "muted"
tint is driven by the desktop `voiceMuted` flag, which is reconciled
**only** by the bridge's blind `muted = !muted` toggle
(`App.jsx:248` ← `bridge/server.ts:487`) and **never** against the
authoritative `/status`. A live capture showed the tray black while
`/status` reported `muted:false, silent_mode:false, speaking:true` — i.e.
JARVIS fully active (even talking) while the icon said "muted." 73 turns
were processed in the prior 2 h. So "black" cannot currently be trusted
to mean "not processing."

## Goal

When the user silences JARVIS by voice, **no audio leaves the machine and
no tokens are spent**, while **voice-wake ("Jarvis") still works**. And
the tray must tell the truth: black ⇒ not sending audio to the cloud /
not processing.

## The core constraint

`silent_mode` keeps the mic on *on purpose* — so it can still hear the
wake phrase. The fix must preserve voice-wake without streaming audio to
the cloud. We do that with a **local** wake-listener (faster-whisper,
already on this branch) instead of cloud STT.

## Architecture A (chosen): gate at the mic, wake locally

When `silent_mode` is on, the **voice-client** stops publishing the mic
to the SFU and instead feeds frames to a local faster-whisper
wake-listener. On hearing the wake phrase it clears `.silent-mode`,
resumes publishing, and asks the agent to voice a brief ack. The
voice-agent's STT chain is **never touched** — it simply goes idle (no
audio → no Deepgram → no honcho → no LLM).

Rejected alternative **B** (dispatch the STT object so silent mode swaps
Deepgram→local): it would require surgery on the barge-in-critical
Deepgram streaming path (the most warned-about surface in the repo) and
audio would still flow + be processed. A isolates all new code in the
voice-client, only active during silent mode, and gives a stronger
privacy guarantee.

## Phase 1 — honest indicator + honcho gate (low-risk, ships first)

No STT/barge-in changes. Immediate relief.

### 1. Honest indicator (desktop-tauri, FROZEN-safe)
- In `App.jsx`, reconcile `voiceMuted` from `/status` (the hook already
  polls `:8767/status`, which carries `muted`). Expose `muted` from
  `useVoiceClient.js` and drive the tray's muted decision from the
  authoritative real-mic state + `silent_mode`, not the stuck bridge
  flag. Keep the bridge `voice_muted` event as an optimistic immediate
  update, but let `/status` correct it every 100 ms so it can never
  stick.
- **No tray colours/states/ring/poll/icon change** — black still means
  "muted/silent." This is state-*derivation* only, inside the existing
  rule. (The FROZEN rule is satisfied: per the 2026-06-18 ask-first
  decision, no new colour is added.)
- Pill text distinguishes the two: `SILENT · listening for "Jarvis"`
  when `silent_mode`, `MUTED` when hard-muted.

### 2. honcho gate (voice-agent)
- At `jarvis_agent.py:6561`, skip `memory_provider.sync_item_async(...)`
  when `_is_silent()`. Cheap belt-and-suspenders against the OpenAI
  deriver cost (moot once Phase 2 stops transcripts entirely, but
  correct on its own and protects the hard-`muted` path too).

## Phase 2 — local wake-listener (voice-client only)

Gated behind `JARVIS_SILENT_LOCAL_WAKE=1`. When the flag is off, behavior
is unchanged (current leak, but safe) — the mic-gating and the
wake-listener are coupled under this one flag so we never produce a
"silenced with no way to voice-wake" state.

### 3. Wake-listener
- **Shared wake patterns:** extract `_WAKE_PATTERNS` (+ the matcher) from
  `jarvis_agent.py` into a tiny module both processes import, so the
  wake vocabulary lives in one place.
- **Mic pump branch** (`jarvis_voice_client.py:~1101`): when
  `state.silent_mode` (already tracked; surfaced in `/status`), do **not**
  publish; instead push the frame into a bounded ring buffer for the
  wake-listener. (`state.muted` keeps its existing hard-drop.)
- **Background transcribe:** a separate task drains the buffer, gates
  speech segments by the existing raw-RMS signal (`_mic_cb` already
  computes it) so silence isn't transcribed, and runs faster-whisper in a
  `to_thread`/executor — **never** in the PortAudio callback (mirrors the
  existing playback `to_thread` pattern; blocking the audio thread is the
  documented mic-drain failure mode).
- **Model:** faster-whisper `small` (the branch's local rung), **lazy-
  loaded on first silent entry** so there's no always-on memory cost.
- **On wake match:** delete `~/.jarvis/.silent-mode` (the universal
  signal both processes read), resume publishing, and POST the agent to
  voice a brief "I'm back" ack (reuses the existing `/speak` path rather
  than re-deriving the agent-side ack at `jarvis_agent.py:4262`).

## Verification plan

- **pytest (voice-agent):** shared wake-pattern matcher (positive: "jarvis",
  "hey jarvis", "wake up"; negative: "you don't have to wake up"); honcho
  gate skips sync when `_is_silent()`; indicator reconciliation logic unit
  (muted derives from `/status`, not the stuck flag).
- **desktop:** `npm run build` (vite) green; manual — tray goes black only
  when `/status` says muted/silent, and recovers to green when active.
- **Manual / live (the real proof):** with `JARVIS_SILENT_LOCAL_WAKE=1`,
  enter silent mode → confirm Deepgram WS traffic / STT activity drops to
  **zero** (no transcripts in the log) → say "Jarvis" → confirm it wakes
  and acks → confirm barge-in is unchanged when active.

## Explicitly NOT touched

- Tray colours / ring / poll rate / `icons/tray.png` (FROZEN).
- The Deepgram streaming barge-in path / FallbackAdapter / VAD wrapping /
  `min_words` / interrupt tuning.
- `src/cli` (the bridge) — the indicator fix is desktop-side only.

## Rollback

- Phase 2 is a single env flag: `JARVIS_SILENT_LOCAL_WAKE=0` restores
  prior behavior with no redeploy.
- Phase 1 indicator/honcho changes are small, additive, and revert
  cleanly per-commit; the honcho gate is a guarded early-return.
