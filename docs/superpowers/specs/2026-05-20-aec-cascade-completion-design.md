# JARVIS AEC Cascade Completion + Runtime Health-Gate — Design Spec

**Date:** 2026-05-20 (corrected PM after as-built verification)
**Status:** Phase A shipped; Phase B (L3) gated on a live soak.
**Authors:** Ulrich (decisions), Claude (drafting)

**Extends (architecture inherited):** [2026-05-19-echo-cancellation-cascade-design.md](2026-05-19-echo-cancellation-cascade-design.md) — the 3-layer cascade, DTLN-aec for L3, one-linear-AEC (§4.1), reverse-stream pattern, output-profile detection.

> **Correction note (2026-05-20 PM).** Earlier drafts of this spec asserted "L1 not loaded" and "the AEC scripts don't exist." Both were **wrong** — artifacts of `pactl` being absent on this box and an `ls` miss. Verified ground truth is in §1. The genuinely-remaining work is **L3 (DTLN)** plus soak-validation; almost everything else already exists or shipped this session.

---

## 1. As-built reality (verified via pw-dump / wpctl / git)

The box is **PipeWire-native — there is NO `pactl`** (use `pw-dump`/`pw-cli`/`wpctl`).

**Already in place (cascade Phase 1, committed 2026-05-20 ~00:xx + this session):**
- **L1 (PipeWire `module-echo-cancel`): loaded AND tuned.** A persistent drop-in `~/.config/pipewire/pipewire.conf.d/99-echo-cancel.conf` defines `echo-cancel-source` + `echo-cancel-sink` with **WebRTC-correct args** (`webrtc.extended_filter=true`; NS/HPF/AGC off — owned by the APM layer; **no Speex-only `filter_size_ms`**). Both are the system **default**; the voice-client captures `pulse`→`echo-cancel-source` and plays `pulse`→`echo-cancel-sink`, so L1 has the cancelled mic AND the playback reference.
- **`bin/jarvis-aec-reload`** (committed `90797a78`/`0f90f96e`) — writes/refreshes that drop-in with backup/restore + restarts pipewire; verifies the source returns. WebRTC-correct.
- **`bin/jarvis-aec-soak`** (committed `26d83ab5`/`0f90f96e`) — `[hours]` arg, self-healing column migration, output-profile distribution, layer activation rates, APM-delay/DTLN-latency rollups, and the echo-loop detector.
- **Reverse-stream reference** (`audio/apm_reverse_stream.py`) — complete; the 16 kHz reference ring + delay estimator are fed **ungated** in the playback loop (`jarvis_voice_client.py:464-472`).
- **Telemetry columns** (`turns.aec_layer{1,2,3}_active`, `output_profile`, `apm_delay_ms_p50`, `dtln_latency_ms_p95`) + the `aec_state.py` cross-process bridge.

**Shipped THIS session (2026-05-20 PM — the runtime health-gate, Units A+B):**
- `audio/aec_health.py` — `l1_echo_cancel_active()` (pw-dump probe of the default source), `EchoDefense`, `current_echo_defense()`, `sufficient_for_hot_mic()` (deny-by-default). Commits `bdc15325`, `46231574`.
- `jarvis_voice_client.py` — mic-gate keys on **measured** `EchoDefense` (not env flags); `l1_active` telemetry from the real probe (was the cosmetic `JARVIS_PIPEWIRE_AEC` read); `state.speaking` driven by the **outgoing TTS render signal** (`audio/speaking_signal.py`), not mic RMS. Commits `3cf2a7b5`, `798423f3`, `b93cd898`.

**NOT built (the genuine remaining work):**
- **L3 (DTLN neural residual)** — `audio/dtln_aec.py` + `models/dtln_aec_128.onnx` absent. `onnxruntime` 1.26.0 IS installed.
- **Soak-validation + promotion** — `sufficient_for_hot_mic` is `_HOT_MIC_SET="none"` (deny). No set has been soak-validated, so hot-mic (barge-in) is still off.

**The problem that remains:** L1 (linear) leaves residual that can make Whisper hallucinate when the mic is hot during TTS (the 2026-05-20 14:43–14:48 failure). With the health-gate now deny-by-default, the mic drops during speak (safe, no barge-in) — same as the live mitigation. Restoring barge-in needs the residual low enough that the soak passes — either tuned-L1 proves sufficient, or L3 is required.

---

## 2. Goals
- **G1.** Restore barge-in: mic hot during TTS on speakers with echo cancelled cleanly enough that STT doesn't degrade.
- **G2.** The regression can't recur: the hot-mic decision keys on **measured** state AND a **soak-validated-sufficient** layer set; any shortfall → mic-drop. *(Shipped — `aec_health.py`.)*
- **G3.** Truthful telemetry: `aec_layer*_active` reflect reality. *(Shipped — `l1_active` from the probe.)*
- **G4.** Safe rollout: hot-mic ships default-off; a soak proves ≈0 during-speak STT transcripts before the default flips.
- **G5.** Robust fallback: mic-drop fallback can't false-mute the user. *(Shipped — `state.speaking` from render signal.)*

## 3. Non-goals
- NG1. Re-deciding the cascade architecture. NG2. Training a custom AEC model (use pre-trained DTLN-aec). NG3. Browser mis-routing / Deepgram IPv6 DNS (separate). NG4. Non-PipeWire stacks (graceful degrade to mic-drop).

## 4. Unifying principle
"Echo defense is *sufficient*" is a measured, soak-validated fact — single source of truth (`aec_health.current_echo_defense` + `sufficient_for_hot_mic`) consumed by both telemetry and the mic-gate. Env flags are *ceilings*, never *promises*. Fail-closed → mic-drop.

## 5. Components — status

### 5.1 ① L1 + truthful telemetry — **DONE**
L1 is loaded + WebRTC-tuned via the existing `99-echo-cancel.conf` (applied by the existing `bin/jarvis-aec-reload`). `l1_active` telemetry now comes from `aec_health.l1_echo_cancel_active()` (real pw-dump probe of the default source), not the env flag. **No L1 work remains** beyond optionally re-running `bin/jarvis-aec-reload` if the tuned drop-in is ever lost.

### 5.2 ② L3 (DTLN) — **REMAINING (gated on §5.4 soak)**
Build `audio/dtln_aec.py::DTLNResidualFilter` (load `dtln_aec_128.onnx`, single-thread onnxruntime, `process(mic16k, ref16k)` on 160-sample frames, per-frame p95 latency → self-disable on breach of `JARVIS_NEURAL_AEC_LATENCY_BUDGET_MS` default 8; expose `healthy`/`p95_ms`). Model download in `install.sh` (pinned + SHA256). Wire into `_mic_cb` after APM when `profile=="speakers"` and `dtln.healthy`, reading the **already-wired** reference ring. Mic-publish 48 kHz→16 kHz (option 2b) with the 48 k+resample fallback (2a). **Only build this if §5.4's soak shows tuned-L1-alone leaves residual.**

### 5.3 ③ Runtime health-gate — **DONE**
`aec_health.current_echo_defense(apm_aec, dtln_healthy) -> EchoDefense(l1,l2_aec,l3)` (measured, fail-closed) + `sufficient_for_hot_mic(d, profile)` (headphones always; speakers only if the validated `_HOT_MIC_SET` is met; deny-by-default). `_should_publish_during_speak` and `_write_aec_state_snapshot` consume it. `dtln_healthy` is wired `False` until §5.2 ships.

### 5.4 ④ Validation + rollout — **soak EXISTS; validation REMAINING**
Use the existing `bin/jarvis-aec-soak [hours]` (echo-loop detector: STT transcripts produced while `state.speaking` must trend to 0). Rollout ladder:
1. Restart the voice-client (the Phase-A code is committed but not live until restart) and soak on speakers with the mic temporarily forced hot (`JARVIS_MIC_DURING_SPEAK=1` in a soak session) → does **tuned L1 alone** drive during-speak transcripts ≈ 0?
2. If yes → promote `aec_health._HOT_MIC_SET = "l1"`. If no → build §5.2 (L3), soak again, promote to `"l1_l3"`.
3. When the chosen set passes, remove the mitigation flags (`JARVIS_NEURAL_AEC=0`, `JARVIS_MIC_DURING_SPEAK=0`) → hot-mic default-on, gated by ③.

### 5.5 ⑤ `state.speaking` reliability — **DONE**
Driven by `audio/speaking_signal.is_rendering_speech` on the outgoing Orpheus PCM (not mic RMS), with a hold — so the mic-drop fallback can't false-mute the user.

## 6. Data flow (the gate decision — as shipped)
```
mic frame (_mic_cb, 10 ms): APM NS/HPF
  → [Phase B: if speakers and dtln.healthy: frame16k = dtln.process(mic16k, ring.read_16k_aligned(adc_t))]
  → if state.speaking and JARVIS_MIC_DURING_SPEAK != 1:
        d = current_echo_defense(apm_aec=(_apm and _APM_AEC), dtln_healthy=<_dtln.healthy | False>)
        if not sufficient_for_hot_mic(d, profile): return    # mic-drop (deny-by-default today)
  → publish → SFU → STT
playback (OutputStream, 10 ms): estimator.note_output + ring.write(16k ref)  [already wired]
  → state.speaking = is_rendering_speech(outgoing PCM) + hold
```

## 7. Error handling / degradation
| Failure | Behavior |
|---|---|
| echo-cancel-source not default (drop-in lost) | `l1=False` → `sufficient_for_hot_mic` false → mic-drop. Re-run `bin/jarvis-aec-reload`. |
| onnxruntime / model missing (Phase B) | L3 disabled + WARN; `l3=False`; gate falls to mic-drop unless L1-alone validated. |
| DTLN p95 > budget | self-disable session; `l3=False`; telemetry. |
| 16 k publish rejected | fallback 2a (48 k + resample). |
| `aec-state.json` stale | agent writes NULLs (existing). |
| exception in defense/gate | fail-closed → mic-drop. |

## 8. Acceptance criteria
- **A1.** `pw-dump`/`wpctl` shows `echo-cancel-source` as the active default; `l1_active` tracks it (unload → 0). *(Probe shipped.)*
- **A2.** Barge-in: "stop" mid-reply interrupts < 500 ms with the validated set active.
- **A3.** `bin/jarvis-aec-soak` reports **0** during-speak transcripts over a 30-min speaker soak with the validated set.
- **A4.** Gate denies (mic-drop) when the validated set isn't met — unit-tested. *(Shipped.)*
- **A5.** DTLN p95 < budget; self-disables on breach. *(Phase B.)*
- **A6.** `state.speaking` from render signal; ambient while silent ≠ speaking. *(Shipped.)*
- **A7.** Telemetry truth across layers. *(l1 shipped; l3 Phase B.)*
- **A8.** Mitigation flags removed only after A3 passes; suite green; new unit tests. *(aec_health + speaking_signal tests shipped; DTLN tests Phase B.)*

## 9. Rollout / phasing
- **Phase A — DONE (code).** Health-gate + measured state + truthful l1 + `state.speaking`. Committed; not live until the next voice-client restart. Deny-by-default, so safe.
- **Phase A checkpoint — NEXT, user-run.** Restart the client, soak on speakers, decide if tuned-L1 alone is clean.
- **Phase B — L3 (DTLN), only if the soak shows residual.** Then re-soak + promote to `"l1_l3"`.
- Flip hot-mic default-on + remove mitigation flags only when the soak passes. **Risk:** if even L1+L3 can't get residual low enough, barge-in stays gated off — the soak decides, not optimism.

## 10. Out of scope / follow-ups
Browser mis-routing; Deepgram IPv6 DNS `EAI_AGAIN`; LiveKit Cloud adaptive interruption.

## 11. References
- [2026-05-19-echo-cancellation-cascade-design.md](2026-05-19-echo-cancellation-cascade-design.md).
- Existing: `bin/jarvis-aec-reload`, `bin/jarvis-aec-soak`, `~/.config/pipewire/pipewire.conf.d/99-echo-cancel.conf`, `audio/apm_reverse_stream.py`, `audio/aec_state.py`, `audio/output_profile.py`.
- Shipped this session: `audio/aec_health.py`, `audio/speaking_signal.py`; commits `bdc15325 46231574 3cf2a7b5 798423f3 b93cd898`.
- breizhn/DTLN-aec; PipeWire `module-echo-cancel`; LiveKit Python APM.
