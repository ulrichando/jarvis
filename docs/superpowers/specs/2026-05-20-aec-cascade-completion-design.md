# JARVIS AEC Cascade Completion + Runtime Health-Gate — Design Spec

**Date:** 2026-05-20
**Status:** Draft (awaiting user review before writing-plans handoff)
**Authors:** Ulrich (decisions), Claude (drafting)

**Extends (architecture inherited, not re-opened):**
[2026-05-19-echo-cancellation-cascade-design.md](2026-05-19-echo-cancellation-cascade-design.md) — its 3-layer cascade, the DTLN-aec choice for L3, the one-linear-AEC decision (§4.1), the reverse-stream pattern, and the output-profile detection are all inherited. This spec corrects the **as-built reality** and adds the pieces that were missing or wrong.

**Related code:**
- `src/voice-agent/jarvis_voice_client.py` — mic gate (`_should_publish_during_speak` L156-170, call site L813-822), audio devices (`AUDIO_INPUT_DEVICE`/`AUDIO_OUTPUT_DEVICE` = `"pulse"` L107-108), APM (L123-129, AEC intentionally off — L1 owns echo), reverse-stream feed (L455-479, ungated), `state.speaking` RMS gate (L206-217), AEC-state snapshot (L940-953), mic `SAMPLE_RATE=48000` (L92).
- `src/voice-agent/audio/apm_reverse_stream.py` — COMPLETE (delay estimator + 16 kHz reference ring).
- `src/voice-agent/audio/aec_state.py` — cross-process telemetry bridge (works).
- `src/voice-agent/audio/output_profile.py` — `classify_output_device` + `watch_for_changes`; **pw-dump-primary (the box has no pactl)**, already detects the echo-cancel virtual sink (`_is_echo_cancel`).
- `install.sh::install_echo_cancel_aec` (L443-450, 716) — guards on `[ -x bin/jarvis-aec-reload ]`.

---

## 1. Problem statement — the as-built reality

The 2026-05-19 cascade spec designed an enterprise-grade 3-layer AEC so the mic could stay hot during TTS (enabling barge-in) without JARVIS hearing its own echo. **Only Phase 1 partially shipped — and it shipped the dangerous half:** the speak-time mic-drop safety net was removed in favour of a gate that keeps the mic hot "when echo defense is active." But that gate trusts env flags, and the cancellation that *is* running isn't good enough. Verified 2026-05-20 via `pw-dump`/`wpctl`:

- **L1 (PipeWire `module-echo-cancel`): loaded AND wired.** An `echo-cancel-source` + `echo-cancel-sink` exist and are the system **default**; the voice-client captures `pulse`→`echo-cancel-source` (cancelled mic) and plays `pulse`→`echo-cancel-sink` (the playback reference) — so L1 has both halves and is genuinely cancelling. (An earlier check via `pactl` reported "not loaded" — false: **this box has no `pactl`**.) BUT it runs with **default args** (the tuned `bin/jarvis-aec-reload` from the 2026-05-19 §5.1 was never created — `install.sh::install_echo_cancel_aec` guards on `[ -x ... ]` and skips), and **linear AEC leaves residual** sufficient to make Whisper hallucinate.
- **L2 (APM AEC): off by design** (`JARVIS_APM_AEC` default 0; APM does NS + HPF, AGC disabled). Intentional — L1 owns echo (see code comment L114-118).
- **L3 (DTLN neural residual): not built.** `audio/dtln_aec.py` + `models/dtln_aec_128.onnx` are absent. `onnxruntime` 1.26.0 *is* installed. The 16 kHz reference ring + delay estimator (`apm_reverse_stream.py`) are complete and fed **ungated** (L464-472) — so L3's hard plumbing already exists.
- **Net: only *linear* (L1) cancellation runs, and its residual garbled STT.** L3 (the neural residual cleaner) is unbuilt, so nothing removes the residual. The live mitigation (mic-drop during speak: `JARVIS_NEURAL_AEC=0` + `JARVIS_MIC_DURING_SPEAK=0`) is what fully protects STT today. (Whether L1 was up during the exact 14:43–14:48 window is unverifiable after the fact — it is now; either way the durable fix is identical.)

### Root cause — the load-bearing bug
"Echo defense is active *and good enough*" is decided from **env flags, not runtime reality**, and the gate accepts *any* claimed layer rather than a *validated-sufficient* set:
1. `_should_publish_during_speak()` keys the hot-mic decision on `JARVIS_APM_AEC` / `JARVIS_NEURAL_AEC` flags.
2. `l1_active` telemetry (L944) reports `JARVIS_PIPEWIRE_AEC`, not whether the echo-cancel-source is actually the active source.
3. `JARVIS_NEURAL_AEC` defaults to `1` while L3 doesn't exist.

The live failure (2026-05-20 14:43–14:48): the user was heard fine, JARVIS spoke, the hot mic (trusting `JARVIS_NEURAL_AEC=1`) captured L1's **residual** echo (linearly cancelled but not clean), STT hallucinated ("…lost a lot of my hair"), JARVIS couldn't understand the user, and the task died. A correct gate would have required a *validated-sufficient* set, found L3 absent, and dropped the mic.

---

## 2. Goals
- **G1.** Restore barge-in: the mic stays hot during TTS on speakers with JARVIS's echo cancelled *cleanly enough that STT doesn't degrade*, so the user can interrupt mid-reply.
- **G2.** The regression cannot recur: the hot-mic decision keys on **measured** state AND a **soak-validated-sufficient** layer set; any shortfall → mic-drop fallback.
- **G3.** Truthful telemetry: `aec_layer{1,2,3}_active` reflect reality (echo-cancel-source active / APM AEC on / DTLN loaded+healthy), not config flags.
- **G4.** Safe rollout: hot-mic ships default-off; a soak proves ≈0 during-speak STT transcripts before the default flips; the mitigation stays until then.
- **G5.** Robust fallback: the mic-drop fallback can't false-mute the user (`state.speaking` from a real signal, not playback RMS).

## 3. Non-goals
- **NG1.** Re-deciding the cascade architecture (inherited from 2026-05-19).
- **NG2.** Training a custom AEC model — use the pre-trained DTLN-aec checkpoint.
- **NG3.** The supervisor's browser mis-routing and the Deepgram IPv6 DNS `EAI_AGAIN` (both separate issues).
- **NG4.** Wayland/non-PipeWire audio specifics — PipeWire (pw-dump/pw-mon present, no pactl) is the target, with graceful degrade (mic-drop) if the echo-cancel source is absent.

## 4. The unifying principle
**"Echo defense is sufficient" must be a measured, validated fact** — surfaced as a single source of truth that BOTH the telemetry and the mic-gate consume. Env flags become *ceilings* (an operator can force a layer off) and never *promises*. The gate accepts only the layer set a soak has proven keeps during-speak STT clean — not merely "some layer is on." Computing the state fails-closed: any error or shortfall → mic-drop.

## 5. Components

### 5.1 Part ① — L1 retune + truthful `l1_active` (L1 is already loaded)
- **New `bin/jarvis-aec-reload`** — idempotent unload+**reload** of `module-echo-cancel` with the 2026-05-19 §5.1 tuned args (`extended_filter=true`, `filter_size_ms=200`, NS/AGC/HPF off — owned by L2). L1 is currently loaded with **default** args; retuning may cut the residual materially. Verifies the `echo-cancel-source` reappears; exits non-zero otherwise. `install.sh::install_echo_cancel_aec` (already present) then actually fires.
- **New `audio/aec_health.py::l1_echo_cancel_active() -> bool`** — via **`pw-dump`/`wpctl`** (the box has no `pactl`): true iff an `echo-cancel-source` exists AND is the default source the client captures from. Short TTL cache; refreshed by the existing pw-mon watcher. This is the REAL L1 signal (replacing the cosmetic `JARVIS_PIPEWIRE_AEC` read at L944). Reuses `output_profile.py`'s pw-dump parsing pattern.
- **`jarvis_voice_client.py`** — `l1_active` (telemetry + gate) = `l1_echo_cancel_active()` **and** `JARVIS_PIPEWIRE_AEC=="1"` (flag as ceiling).
- **Validation checkpoint:** retune L1, run the soak (§5.4) on **L1-only** — does the residual now drop enough for during-speak transcripts ≈ 0? If yes, L3 may be unnecessary (barge-in restored cheaply); if no, Phase B (L3) is justified.

### 5.2 Part ② — L3 (DTLN) build
- **New `audio/dtln_aec.py::DTLNResidualFilter`** — loads `models/dtln_aec_128.onnx`, single-thread `onnxruntime` CPU session; `process(mic16k, ref16k) -> np.ndarray` on 160-sample (10 ms @ 16 kHz) frames; per-frame `perf_counter`; rolling p95 over a 100-frame window; on breach of `JARVIS_NEURAL_AEC_LATENCY_BUDGET_MS` (default 8) self-disable for the session + WARN + telemetry. Exposes `healthy: bool` and `p95_ms`.
- **Model** — downloaded by `install.sh` from a pinned release URL, SHA256-verified. `onnxruntime` import-guarded (auto-disable + WARN if absent).
- **Wire into `_mic_cb`** after APM, when `_current_profile=="speakers"` and `dtln.healthy`: `frame16k = dtln.process(mic16k, _reverse_ringbuf.read_16k_aligned(adc_t))`. The reference ring is already fed (§1).
- **Mic-publish 48 kHz → 16 kHz** (2026-05-19 §5.3 option 2b): change `SAMPLE_RATE`/track config; validate the SFU + agent `AudioStream` accept 16 kHz. **Fallback 2a** if rejected: keep 48 kHz publish, run DTLN on a 16 kHz copy and upsample its output back to 48 kHz.

### 5.3 Part ③ — Unified runtime health-gate (anti-regression core)
- **New `audio/aec_health.py::current_echo_defense() -> EchoDefense`** — a small dataclass of **measured** booleans: `l1` (= `l1_echo_cancel_active()`), `l2_aec` (= APM built with `echo_cancellation=True` ∧ `_APM_AEC`), `l3` (= DTLN loaded ∧ healthy this session). The single source of truth.
- **New `audio/aec_health.py::sufficient_for_hot_mic(d: EchoDefense, profile: str) -> bool`** — encodes the **soak-validated-sufficient** set. Initial (pre-validation) value: `False` everywhere on speakers (deny → mic-drop) until §5.4's soak promotes a set (expected `d.l1 and d.l3`; possibly `d.l1` alone if §5.1 retuning suffices). `headphones` → always sufficient (no echo path). This predicate is the *only* place the validated set is encoded.
- **`_should_publish_during_speak` rewritten:** `headphones` → publish. `speakers`/`unknown` → publish **only if** `sufficient_for_hot_mic(current_echo_defense(), profile)`; else return False → mic-drop. NOT "any layer active" — L1-alone is present yet insufficient (it's what failed). `JARVIS_MIC_DURING_SPEAK=1` remains a force-publish escape hatch for headphone users (unchanged semantics).
- **`write_aec_state` fed from `current_echo_defense()`** so telemetry == gate truth (G3).
- **Fail-safe:** any exception computing the state → treat as insufficient → mic-drop (fail-closed for STT quality).

### 5.4 Part ④ — Validation tooling + rollout
- **New `bin/jarvis-aec-soak`** — rolls up profile distribution, per-layer *measured* on-rates, p50 delay, p95 DTLN latency, mid-session auto-disable count, and the **echo-loop detector**: count of turns with an STT transcript produced while `state.speaking` (must trend to 0). HARD-FAIL (exit 2) on: speakers + hot-mic + during-speak transcript appeared; or DTLN p95 > budget.
- **Rollout ladder:**
  1. Ship ①+③+⑤. L1 retuned; gate measures reality but `sufficient_for_hot_mic` still denies on speakers (mitigation flags stay). Soak with the mic *temporarily* forced hot (test rig / `JARVIS_MIC_DURING_SPEAK=1` in a soak session) → does **tuned L1 alone** drive during-speak transcripts ≈ 0?
  2. If yes → promote `sufficient_for_hot_mic` to `d.l1` on speakers. If no → ship ② (L3 DTLN), soak again, promote to `d.l1 and d.l3`.
  3. When the chosen set passes (≈0 during-speak transcripts, p95 < budget), **remove the mitigation flags** (`JARVIS_NEURAL_AEC=0`, `JARVIS_MIC_DURING_SPEAK=0` in the voice-client unit + `override.conf`) → hot-mic default-on, gated by ③.
- **Kill-switches preserved** (now ceilings): `JARVIS_PIPEWIRE_AEC`, `JARVIS_APM_AEC`, `JARVIS_NEURAL_AEC`, `JARVIS_AEC_FORCE_PROFILE`.

### 5.5 Part ⑤ — `state.speaking` reliability (robust fallback)
- Today `state.speaking` is RMS-gated on the playback track (L206-217) → false-positives on ambient → can mute the user when ③'s mic-drop fallback is active (the bug `JARVIS_MIC_DURING_SPEAK=1` was papering over).
- **Drive it from the playback render path instead:** set `state.speaking` true while actively writing Orpheus PCM segments — gated on the **outgoing TTS signal** (clean and known: it's JARVIS's own audio, computed before `out.write(pcm)`), not the mic's RMS — with a short hold (~1.2 s) to bridge inter-phrase gaps. Net: ③'s fallback drops the mic only while JARVIS is genuinely speaking, never on ambient noise.
- (Later, out of scope: subscribe to the agent's TTS-active state over a LiveKit data channel. The local render signal is sufficient here.)

## 6. Data flow (the gate decision)
```
mic frame (_mic_cb, 10 ms):
  → APM NS/HPF (process_stream)
  → if speakers and dtln.healthy:
        frame16k = dtln.process(mic16k, ring.read_16k_aligned(adc_t))
  → if state.speaking and JARVIS_MIC_DURING_SPEAK != 1:
        d = current_echo_defense()                          # MEASURED, not flags
        if not sufficient_for_hot_mic(d, profile): return   # mic-drop unless the soak-proven set is active
  → publish (16 kHz) → SFU → STT

playback frame (OutputStream, 10 ms):
  → estimator.note_output(dac_t); ring.write(16k ref, dac_t)   # already wired, ungated
  → state.speaking = rendering-Orpheus signal (part ⑤)
```

## 7. Error handling / degradation ladder
| Failure | Behavior |
|---|---|
| `module-echo-cancel` not the active source (unloaded / wrong default) | `l1=False` → `sufficient_for_hot_mic` false → mic-drop during speak (safe). `jarvis-aec-reload` exits non-zero; install logs it. |
| `onnxruntime` missing / model missing or hash-mismatch | L3 disabled + WARN; `l3=False`; gate falls to mic-drop unless L1-alone was validated sufficient. install re-downloads next run. |
| DTLN p95 > budget | self-disable rest of session; `l3=False`; telemetry `dtln_latency_ms_p95`. |
| 16 kHz mic-publish rejected by SFU/agent | fallback 2a (48 kHz publish + 16 kHz DTLN copy + upsample). |
| `aec-state.json` stale (voice-client died) | agent reader writes NULLs (existing behavior). |
| exception in `current_echo_defense()` / `sufficient_for_hot_mic` | fail-closed → mic-drop during speak. |

## 8. Acceptance criteria
- **A1.** `pw-dump`/`wpctl` shows the `echo-cancel-source` as the active default after reload; `l1_active` telemetry tracks actual source presence (unload the module → telemetry flips to 0), not the `JARVIS_PIPEWIRE_AEC` flag.
- **A2.** Barge-in: "stop" during a TTS reply interrupts < 500 ms on speakers with the validated-sufficient set active.
- **A3.** Echo-loop: `jarvis-aec-soak` reports **0** during-speak STT transcripts over a 30-min speaker soak with the validated set.
- **A4.** Health-gate: with the validated set NOT met (e.g., L3 force-disabled when the set is `l1∧l3`), the mic provably drops during speak (no during-speak frames published) — verified by unit test + soak.
- **A5.** DTLN p95 < budget; self-disables on breach (telemetry shows it).
- **A6.** `state.speaking` from the render signal: ambient noise while JARVIS is silent does NOT set `speaking=True` (no false mute).
- **A7.** Telemetry truth: all three `l*_active` columns reflect measured state, validated by toggling each layer.
- **A8.** Mitigation flags removed only after A3 passes; pre-existing voice-agent test suite green; new unit tests for `current_echo_defense`/`sufficient_for_hot_mic`, the DTLN filter, and the L1 probe.

## 9. Rollout / phasing
- **Phase A (①+③+⑤):** retune L1 (already loaded) + truthful telemetry, gate on measured state (`sufficient_for_hot_mic` denying until validated), fix `state.speaking`. Lowest risk; soak tests whether **tuned L1 alone** restores barge-in.
- **Phase B (②):** add L3 DTLN if Phase A's soak still shows during-speak transcripts. Validate again; promote the sufficient set to `l1∧l3`.
- Flip hot-mic default-on (promote `sufficient_for_hot_mic` + remove the mitigation flags) **only** when the soak passes. JARVIS keeps working (mic-drop mitigation) throughout.
- **Risk noted:** if even tuned-L1 + L3 can't get residual low enough for ≈0 during-speak transcripts, barge-in stays gated off (mic-drop) — the soak is the arbiter, not optimism.

## 10. Out of scope / follow-ups
- Supervisor browser mis-routing (separate).
- Deepgram IPv6 DNS `EAI_AGAIN` (separate, minor — failover to Whisper works).
- LiveKit Cloud adaptive interruption (separate).

## 11. References
- [2026-05-19-echo-cancellation-cascade-design.md](2026-05-19-echo-cancellation-cascade-design.md) — inherited architecture, PipeWire `aec.args`, DTLN rationale, latency budget.
- breizhn/DTLN-aec; PipeWire `module-echo-cancel`; LiveKit Python APM reference.
- Code anchors: `jarvis_voice_client.py:{92,107-108,114-129,156-170,455-479,813-822,940-953}`; `audio/{apm_reverse_stream,aec_state,output_profile}.py`; `install.sh:{443-450,716}`.
