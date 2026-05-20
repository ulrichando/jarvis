# JARVIS Echo-Cancellation Cascade — Design Spec

**Date:** 2026-05-19
**Status:** Draft (awaiting user review before writing-plans handoff)
**Authors:** Ulrich (decisions), Claude (drafting)
**Related:**
- Current AEC setup: [src/voice-agent/jarvis_voice_client.py](../../src/voice-agent/jarvis_voice_client.py) (APM block ~L110-135, mic-drop workaround ~L714)
- STT residual handling: [src/voice-agent/pipeline/stt_gate.py](../../src/voice-agent/pipeline/stt_gate.py)
- Research brief: §11 sources (PipeWire module-echo-cancel, LiveKit APM, DTLN-aec, AEC-Challenge)
- LiveKit canonical reverse-stream pattern: `.venv/.../livekit/rtc/media_devices.py:155-227, 470-515`

---

## 1. Problem statement

JARVIS's mic picks up its own Groq Orpheus TTS output through laptop speakers. The current defenses are incomplete:

1. **PipeWire `module-echo-cancel`** (WebRTC AEC3) is loaded system-wide but with default args — leaves audible residual that the STT gate must filter.
2. **LiveKit WebRTC APM** runs NS + AGC + HPF on the mic in the voice-client, but **AEC is OFF** (`JARVIS_APM_AEC=0`) because driving it requires feeding playback frames through `process_reverse_stream`, which was never wired.
3. **Workaround** (2026-05-16): the voice-client DROPS all mic frames while `state.speaking == True`. This stops the echo-loop (JARVIS transcribing itself) but **kills barge-in entirely** — the user can't interrupt JARVIS mid-reply. Marked in-code as "Acceptable until working AEC lands (Q2 roadmap item)."

### Symptoms to eliminate (user picked "all three — comprehensive overhaul, enterprise grade")
- **S1**: No barge-in during TTS playback (the mic-drop regression).
- **S2**: JARVIS hears + transcribes himself when echo leaks through (self-loop).
- **S3**: STT accuracy degraded by AEC residual (misheard transcripts).

### Constraints
- **Hardware**: must work everywhere via runtime auto-detect (built-in laptop mic+speakers, USB mic, headphones).
- **Latency**: target < ~10 ms added on the mic path on commodity Intel laptop CPU.
- **Resilience**: enterprise-grade — degrade gracefully, never crash the audio stream, worst case == today's behavior.

---

## 2. Goals

- **G1.** Barge-in works during TTS playback on speaker hardware — the `state.speaking` mic-drop is removed; AEC keeps JARVIS from hearing himself.
- **G2.** Three-layer cascaded AEC: L1 PipeWire AEC3 (primary linear) + L2 APM NS/AGC/HPF (AEC A/B-toggleable) + L3 DTLN-aec neural residual (speakers-only). Each independently kill-switched + telemetrically observable.
- **G3.** Runtime output-device auto-detect (`headphones`/`speakers`/`unknown`) gates L3 and adjusts strategy; re-detects on hot-plug.
- **G4.** Cross-process AEC telemetry: voice-client publishes layer state to a JSON file; agent copies it into per-turn `turns` columns.
- **G5.** Every layer A/B-validated against real during-speak STT-false-transcript rate before defaulting ON — no layer ships on assumption.
- **G6.** Degradation ladder: losing any layer leaves a working path; all-off == today's mic-drop fallback.

## 3. Non-goals

- **NG1.** Replacing PipeWire or the LiveKit audio stack. We tune + extend, not rebuild.
- **NG2.** Hardware procurement (USB mic-arrays, etc.). Auto-detect must work with whatever's present; we don't require new hardware.
- **NG3.** Training a custom AEC model. We use the pre-trained DTLN-aec checkpoint.
- **NG4.** Server-side AEC. LiveKit Cloud doesn't do echo cancellation (only Krisp/ai-coustics NS); echo is killed client-side. Confirmed in research §D.
- **NG5.** Changing the Orpheus TTS playback format (48 kHz mono stays). Only the *mic-publish* format may change to 16 kHz (see §5.3).

---

## 4. Architecture

Three-layer cascade. Mic capture is LOCAL (sounddevice in the voice-client), so all processing happens on one machine with one clock and one local playback reference. This is why the design uses **one primary linear AEC (L1)**, not two — see §4.1.

```
L1 PipeWire AEC3 (system, primary linear)  →  echo-cancel-source
   ↓ (sounddevice reads the cleaned mic here)
L2 LiveKit APM: NS + AGC + HPF   [AEC OFF default; A/B toggle]
   ↓ 48kHz NS/AGC/HPF'd mic
L3 DTLN-aec residual @ 16kHz   [speakers-only, auto-detect gated]
   ↓ residual-free mic
publish 16kHz track → SFU → STT (Deepgram/Whisper)

Playback path (the reference feed):
Orpheus 48kHz chunk → OutputStream cb:
   apm.process_reverse_stream(frame)   [feeds L2 AEC if A/B-on]
   reverse_ref_ringbuffer.write(frame) [feeds L3 reference, downsampled to 16k]
   delay_estimator.note_output(dac_time)
   → echo-cancel-sink → physical speaker
```

### 4.1 Why one linear AEC, not two (resolves the dual-AEC risk)

The mic is captured locally and APM runs locally *before* the SFU. So L1 (PipeWire AEC3) and a hypothetical L2 AEC3 would both be local linear cancellers using the *same* local playback reference. Two linear AEC3 stages in series with the same reference are not reliably additive — the second stage's adaptive filter sees an echo already processed by a non-linear canceller, which can add artifacts. (The research's "winning pattern" of PipeWire + APM assumed APM runs *post-SFU* with a jitter-aligned reference — not JARVIS's local-capture topology.)

**Decision**: L1 is THE linear AEC. L2 runs NS/AGC/HPF only, with AEC behind a runtime toggle (`JARVIS_APM_AEC=1`) used purely for A/B measurement. L3 (DTLN) provides the *non-linear* residual cancellation that L1's linear filter can't — that's genuinely additive, not redundant.

### 4.2 Kill-switch + degradation ladder

| Config | Path | Quality |
|---|---|---|
| All on | L1 + L2(NS/AGC/HPF) + L3 | best |
| `JARVIS_NEURAL_AEC=0` | L1 + L2(NS/AGC/HPF) | very good |
| `JARVIS_PIPEWIRE_AEC=0` | L2(NS/AGC/HPF) + L3 | good* |
| `JARVIS_APM_AEC=1` (A/B) | L1 + L2(+AEC) + L3 | measure vs default |
| All AEC off | mic-drop-while-speaking fallback | safe, no barge-in (today's behavior) |

\* L3 needs the playback reference, which the reverse-stream feeds regardless of L1/L2 — so L3 works even if L1 is off.

Env vars:
- `JARVIS_PIPEWIRE_AEC` (default 1) — L1 tuning applied at install/reload.
- `JARVIS_APM_AEC` (default 0) — L2 AEC A/B toggle. NS/AGC/HPF always on (existing `JARVIS_APM_NS/AGC/HPF`).
- `JARVIS_NEURAL_AEC` (default 1) — L3 enable.
- `JARVIS_NEURAL_AEC_LATENCY_BUDGET_MS` (default 8) — L3 self-disable threshold.
- `JARVIS_AEC_FORCE_PROFILE` (unset|headphones|speakers|auto) — override auto-detect.
- `JARVIS_APM_DELAY_BIAS_MS` (default 0) — manual delay offset.
- `JARVIS_MIC_DURING_SPEAK` — RETIRED as a manual flag; mic-during-speak is now the default on speakers (AEC handles echo). The flag's old meaning is superseded; document the removal.

---

## 5. Components

### 5.1 Layer 1 — PipeWire tuning
**Files modified:** `install.sh` — update the `module-echo-cancel` load with:
```
aec.args = "webrtc.extended_filter=true webrtc.high_pass_filter=false webrtc.noise_suppression=false webrtc.gain_control=false filter_size_ms=200 monitor.mode=true"
```
NS/HPF/AGC explicitly OFF at L1 (owned by L2 — avoids double-processing per research §A anti-pattern). `filter_size_ms=200` for the laptop echo path (>50 ms tail). `monitor.mode=true` for a better reference when other apps play through the same sink.

**New file:** `bin/jarvis-aec-reload` — idempotent unload + reload of `module-echo-cancel` with the tuned args. Used by install.sh + manual retuning.

### 5.2 Layer 2 — APM NS/AGC/HPF + reverse-stream wiring
**Files modified:** `src/voice-agent/jarvis_voice_client.py`:
- `_APM_AEC` stays default 0 (A/B toggle).
- REMOVE the `state.speaking == True → return` mic-drop branch in `_mic_cb` when on speakers (replaced by AEC-based barge-in). On `headphones`/`unknown` with AEC fully off, the mic-drop fallback remains as the safety net.
- ADD `set_stream_delay_ms` call before `process_stream`, fed by the delay estimator.

**New module:** `src/voice-agent/audio/apm_reverse_stream.py`:
- `class APMDelayEstimator` — port of LiveKit's internal estimator (`media_devices.py:478-510`); rolling `(output_dac, input_adc)` pairs → `current_delay_ms() -> int`, clamped [0, 500], `+ JARVIS_APM_DELAY_BIAS_MS`.
- `class ReverseRefRingBuffer` — pre-allocated numpy ring + single `threading.Lock`. `write(frame, dac_ts)` (OutputStream thread), `read_16k_aligned(input_ts) -> np.ndarray` (InputStream thread; returns the 16 kHz-downsampled reference aligned to the current mic frame).
- `wire_output_stream(apm, sink, ringbuf, estimator)` — sets up the sounddevice OutputStream callback that calls `apm.process_reverse_stream` + `ringbuf.write` + `estimator.note_output` per 10 ms playback chunk.

### 5.3 Layer 3 — DTLN-aec residual @ 16 kHz
**Decision (Fix #2, option 2b):** publish a **16 kHz** mic track to the SFU (Deepgram + Whisper consume 16 kHz natively; today's 48 kHz is downsampled by them anyway). DTLN runs natively at 16 kHz — NO resampling on the mic path. The reverse-reference is downsampled 48k→16k once in the ring buffer's write path (cheap, off the mic-latency path).

**Files created:**
- `src/voice-agent/audio/dtln_aec.py` — `class DTLNResidualFilter`: loads `models/dtln_aec_128.onnx`, single-threaded onnxruntime CPU session. `process(mic16k: np.ndarray, ref16k: np.ndarray) -> np.ndarray` on 160-sample (10 ms @ 16 kHz) frames. Per-frame `perf_counter` timing; p99 over 100-frame window > budget → self-disable for the session + WARN + telemetry.
- `models/dtln_aec_128.onnx` (~2 MB) — downloaded by install.sh from a pinned release, SHA256-verified.

**Files modified:** `jarvis_voice_client.py::_mic_cb` — after APM, if `JARVIS_NEURAL_AEC` and `profile == "speakers"`: `frame16k = dtln.process(mic16k, ringbuf.read_16k_aligned(ts))`.

**Mic-publish format change:** the published track switches 48 kHz → 16 kHz. Validate against SFU + agent-side `AudioStream` consumption (the STT plugins already expect ≤16 kHz). Orpheus playback (48 kHz) is on the separate output path — untouched.

### 5.4 Auto-detect output profile
**New file:** `src/voice-agent/audio/output_profile.py`:
- `classify_output_device() -> Literal["headphones","speakers","unknown"]` — parses `pactl list sinks` active port `port.type` + `device.form_factor`. 30 s TTL cache.
- `watch_for_changes(cb)` — line-buffered `pw-mon` subprocess; parses `node-changed`/`port-changed`; invokes cb. Spawned at voice-client startup.
- `JARVIS_AEC_FORCE_PROFILE` override short-circuits classification.

### 5.5 Cross-process telemetry (Fix #3)
**Voice-client writes** `~/.jarvis/aec-state.json` (atomic write → rename) on profile change + every N seconds:
```json
{"output_profile":"speakers","l1_active":true,"l2_aec_active":false,
 "l3_active":true,"apm_delay_ms_p50":42,"dtln_latency_ms_p95":3.1,
 "updated_utc":"2026-05-19T..."}
```
**Agent reads it** at turn-write time (mirrors the T11 `compute_confab_check_state` pattern). Stale guard: `updated_utc` > 60 s old → write NULLs.

**Files modified:** `src/voice-agent/pipeline/turn_telemetry.py` — online migration adds `turns` columns: `aec_layer1_active`, `aec_layer2_aec_active`, `aec_layer3_active` (INTEGER), `output_profile` (TEXT), `apm_delay_ms_p50`, `dtln_latency_ms_p95` (INTEGER/REAL). `log_turn` accepts the new optional kwargs.

**New script:** `bin/jarvis-aec-soak` — rolls up profile distribution, layer on-rates, p50/p95 delay, p95 DTLN latency, mid-session auto-disable count, and the **echo-loop detector** (count of turns with an STT transcript produced *during* `state.speaking` — should trend to 0). HARD-FAIL (exit 2) on: speakers + L3 off + during-speak transcript appeared; or DTLN p95 > budget.

---

## 6. Data flow

### 6.1 Mic capture
```
sd InputStream cb (_mic_cb, 10ms)
  → raw RMS (pre-everything, listening indicator)
  → [L1 already applied: device IS echo-cancel-source]
  → apm.set_stream_delay_ms(estimator.current_delay_ms())
  → apm.process_stream(frame)       # NS+AGC+HPF (+AEC if A/B on)
  → profile = output_profile.classify()  (cached)
  → if NEURAL_AEC and profile==speakers:
        frame16k = dtln.process(mic16k, ringbuf.read_16k_aligned(ts))
  → publish 16k frame → SFU → STT
```

### 6.2 Playback reference feed
```
Orpheus 48k chunk → sd OutputStream cb (10ms)
  → apm.process_reverse_stream(frame)        # L2 AEC reference
  → ringbuf.write(downsample_48k_to_16k(frame), dac_ts)  # L3 reference
  → estimator.note_output(dac_ts)
  → echo-cancel-sink → speaker
```

### 6.3 Barge-in (the payoff)
```
JARVIS speaking + user "stop"
  → mic frames KEEP flowing (no drop on speakers)
  → L1 + L3 remove JARVIS's echo; only "stop" survives
  → Silero VAD fires on the clean frame → existing
    _on_user_state_for_interrupt → session.interrupt()
  → Orpheus upstream-cancel stops TTS
```

### 6.4 Hot-plug
```
pw-mon "port-changed" → watch_for_changes cb → re-classify
  → speakers→headphones: L3 stops (no echo path)
  → headphones→speakers: L3 resumes
  → aec-state.json updated → next turn's telemetry reflects it
```

---

## 7. Error handling

| Failure | Layer | Behavior |
|---|---|---|
| `dtln_aec_128.onnx` missing / hash mismatch | L3 | Disable L3 for session; WARN; L1+L2 run. install.sh re-downloads next run. |
| onnxruntime not installed | L3 | Import-guarded; `JARVIS_NEURAL_AEC` auto-0; WARN once at boot. |
| DTLN p99 > budget over 100-frame window | L3 | Self-disable rest of session; telemetry `dtln_latency_ms_p95`; WARN. |
| `process_reverse_stream` raises (size mismatch) | L2 | Per-frame catch, DEBUG log; APM continues with stale reference (→ degrades toward no-AEC). |
| `set_stream_delay_ms` wild value (clock skew) | L2 | Clamp [0,500]; estimator window reset. |
| `pactl`/`pw-mon` unavailable (non-PipeWire) | detect | `classify` → "unknown" → conservative speakers path; watcher logs WARN, doesn't spawn; `JARVIS_AEC_FORCE_PROFILE` escape hatch. |
| `module-echo-cancel` fails to load | L1 | install.sh logs error, continues; L2/L3 become primary defense (L3 still has reference). |
| Headphones detected but echo leaks (open-back) | detect | `JARVIS_AEC_FORCE_PROFILE=speakers` forces full cascade. |
| `aec-state.json` stale (voice-client died) | telemetry | Agent reader writes NULLs for AEC columns; no crash. |
| 16k mic-publish rejected by SFU/agent | L3/publish | Fall back to 48k publish + inline DTLN resample (option 2a) OR disable L3. Validate during testing before committing 2b. |
| Ring buffer underrun (read with no aligned ref) | L3 | Return zeros as reference → DTLN passes mic through ~unchanged (no residual subtraction that frame). No crash. |
| Both APM AEC + mic-drop active | L2 | Guard: mic-drop branch only runs when `_APM_AEC == 0 AND profile != headphones`. Can't double-apply. |

---

## 8. Acceptance criteria

- **A1.** On speaker hardware, the `state.speaking` mic-drop no longer fires; mic frames flow during TTS (verified: `source.capture_frame` called while `state.speaking=True`).
- **A2.** Barge-in: saying "stop" during a TTS reply interrupts JARVIS in < 500 ms (hardware checklist item 3).
- **A3.** Echo-loop: zero STT transcripts produced during `state.speaking` over a 30-min speaker soak (the `jarvis-aec-soak` echo-loop detector reads 0).
- **A4.** L3 DTLN runs at 16 kHz natively, p95 latency < `JARVIS_NEURAL_AEC_LATENCY_BUDGET_MS` (8 ms default); self-disables on breach.
- **A5.** Auto-detect classifies headphones vs speakers correctly; hot-plug re-detect within one `pw-mon` event; L3 gates on profile.
- **A6.** Cross-process telemetry: every turn written while the voice-client is live carries non-NULL `output_profile` + layer-active columns; stale file → NULLs.
- **A7.** Degradation ladder: each kill-switch combination yields a working path; all-AEC-off restores today's mic-drop fallback.
- **A8.** A/B gates: L3 (and L2-AEC if toggled) ship ON only after a soak shows they beat OFF on during-speak false-transcript rate.
- **A9.** Pre-existing voice-agent test suite still passes; new unit + integration tests added.

---

## 9. Rollout + kill-switches

**Phase 1 (L1 + L2 wiring, lowest risk):** PipeWire tuning + reverse-stream + delay estimator + remove mic-drop on speakers + auto-detect + cross-process telemetry. This alone restores barge-in and should fix S1/S2 substantially. Validate via soak before Phase 2.

**Phase 2 (L3 neural residual):** DTLN at 16 kHz + 16k mic-publish + budget guard. A/B against Phase-1-only. Ship L3 ON only if it measurably reduces during-speak false-transcripts.

**Kill switches:** `JARVIS_PIPEWIRE_AEC`, `JARVIS_APM_AEC`, `JARVIS_NEURAL_AEC`, `JARVIS_AEC_FORCE_PROFILE`, `JARVIS_NEURAL_AEC_LATENCY_BUDGET_MS`, `JARVIS_APM_DELAY_BIAS_MS`. Each disabling preserves the others; bottom of the ladder == today's behavior.

---

## 10. Out-of-scope follow-ups (logged)
- USB mic-array beamforming (if the user adds hardware later).
- AEC3 args validation: the PipeWire man page doesn't enumerate `aec.args` keys; the chosen keys are inferred from libspa-aec-webrtc source — validate against `pw-cli ls Module` at install time (research §C uncertainty flag).
- Replacing DTLN with a 2024/2025 AEC-Challenge winner once a maintained PyPI inference path exists (none as of 2026 — research §B).

---

## 11. References
- [PipeWire module-echo-cancel docs](https://docs.pipewire.org/page_module_echo_cancel.html)
- [Arch manpage libpipewire-module-echo-cancel(7)](https://man.archlinux.org/man/libpipewire-module-echo-cancel.7.en)
- [LiveKit Python APM reference](https://docs.livekit.io/reference/python/livekit/rtc/apm.html)
- [LiveKit noise & echo cancellation guide](https://docs.livekit.io/transport/media/noise-cancellation/)
- [Switchboard Audio: how WebRTC AEC3 works](https://switchboard.audio/hub/how-webrtc-aec3-works/)
- [breizhn/DTLN-aec](https://github.com/breizhn/DTLN-aec)
- [Microsoft AEC-Challenge](https://github.com/microsoft/AEC-Challenge)
- [SMRU joint AEC+NS (2024)](https://arxiv.org/html/2406.11175v2)
- [OpenAI Realtime conversations](https://platform.openai.com/docs/guides/realtime-conversations)
- LiveKit canonical reverse-stream wiring: `.venv/.../livekit/rtc/media_devices.py:155-227, 470-515`
- Current AEC code: `src/voice-agent/jarvis_voice_client.py` (APM block, mic-drop workaround)
