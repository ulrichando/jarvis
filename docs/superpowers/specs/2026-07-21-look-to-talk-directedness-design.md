# Look-to-Talk Directedness + Engagement Indicator — Design

**Date:** 2026-07-21
**Status:** Design v2 (three adversarial Fable fact-checks folded in — voiceprint refuted, look-to-talk feasibility verified, spec review found + fixed 3 blocking defects). Awaiting user review → implementation plan.

## Problem

In a shared/noisy room (Ulrich's family talking, kids, TV), JARVIS's desktop voice
mode either **drops everything** (the addressing gate silently rejects every
non-vocative turn — the live 2026-07-21 "JARVIS not talking" incident, where a whole
family conversation was transcribed and discarded) or, loosened, would **answer the
room**. The gate is **text-only** — no idea *who* is speaking or *whether they're
addressing JARVIS*. Ulrich wants JARVIS to respond to **him, hands-free, without a wake
word**, and ignore the room — plus a **state indicator** that shows when JARVIS is
engaged with him vs. hearing ambient it will drop.

## Non-goals / rejected approaches

- **Wake word required every turn** — rejected by the user.
- **Speaker verification (voiceprint) as identity** — refuted: same-household voices run
  10–30× worse than VoxCeleb; overlap blends the embedding → false-rejects the owner.
  Faces distinguish family reliably; voices don't. Pivoted to vision.
- **Full-duplex desktop** — tried + reverted 2026-07-21 (hot mic drank the room);
  half-duplex stays. (Android is already full-duplex via platform AEC.)
- **True eye-gaze** — wrong at desktop distance; head-facing-screen is the right proxy.

## Dependencies (must be stated up front)

- **Phase 0 (indicator)** depends only on `_is_unaddressed_ambient` — **exists on master**.
- **Phases 1–3 (look-to-talk)** fuse into `_addressing_decision` + `pipeline/directedness.py`,
  which exist **only on open PRs #286 (fix/voice-ambient-gate) and #294
  (feat/voice-directedness-gate), NOT master.** Those must merge first. Until then,
  Phase 1 targets `_is_unaddressed_ambient` directly and treats #294's score as optional.
- **New runtime dep:** `mediapipe==0.10.35` + the `face_landmarker.task` model (see Install).

## Core principle (from the Look-and-Talk fact-check)

**Visual attention ≠ who is speaking.** Someone facing the screen while talking to a kid
passes every visual test. Google's Look-and-Talk needed *active-speaker detection* (lip
motion bound to audio) + a directedness model on top of gaze+face. The unlock is a
**fusion, additive and fail-open**, and every visual signal is scoped to the **owner's
face-track** (not "any face in frame"):

```
owner-track present AND owner-track facing (head-pose cone)   [WHO + attention, owner-scoped]
  AND owner-track lip-active during the speech-onset window    [the OWNER is the one talking]
  AND directedness score OK (#294)                             [it's a request, not chatter]
  → accept the turn with NO wake word
```

Any leg failing, or the camera dark/stale/no-owner-face → **fail open to today's
behavior** (vocative / wake / engagement window). Never more deaf than now.

**Honest residual false-accepts (stated because they're unavoidable):**
- **Owner faces the screen while talking TO someone else** — passes all visual legs; only
  the #294 *text* heuristic guards it, and "can you stop that" (said to a kid) scores as a
  command. This is a real false-accept the design cannot fully close without audio-bound
  ASD (out of scope).
- **Bare lip-motion is not audio-bound** — owner mouthing/chewing while a kid talks could
  register lip-activity on the owner track. Mitigated by requiring lip-activity to overlap
  the VAD speech window, but not eliminated.

## Architecture

Four units, each independently testable.

### 1. Passive attention loop — `vision/attention_tracker.py`
Replaces the presence-only `vision/person_tracker.py`. Runs as a **dedicated thread inside
the voice-client** (the designed camera owner; the tracker is opt-in and currently off, so
`/dev/video0` is free). In-process is empirically safe — mediapipe/YuNet/SFace **release the
GIL** during native inference (measured: a Python spin-thread kept 89% throughput during
mediapipe inference), and person_tracker already ran as a client daemon thread. **Isolation
requirement:** the CV loop must not starve the client's PortAudio mic drain / `:8767` event
loop (documented starvation history at jarvis_voice_client.py:969/1215/1340) — run it on its
own thread with a CPU-budget watchdog; **fallback** = a dedicated `python -m
vision.attention_tracker` subprocess writing `attention.json` (the V4L2 one-opener only
requires *an* owner, not the client specifically).

Per frame (~5 Hz, MediaPipe **VIDEO** running_mode for temporal smoothing; FaceLandmarker is
**not thread-safe** → single capture thread):
- **YuNet** detect (kept because `cv2.FaceRecognizerSF.alignCrop` needs YuNet's 15-element
  face row; MediaPipe also embeds its own BlazeFace detector — accept the double-detect, or
  synthesize the row from MediaPipe landmarks and drop YuNet: implementer picks one, ~stated).
- **Face-track association (NEW greenfield sub-component, ~50–100 lines)** — greedy IoU/
  centroid matching of detections across frames (200 ms apart), track birth/death, identity-
  swap handling when two faces cross, re-ID trigger on track loss. **This is real new code
  with real failure modes (owner identity mis-assigned to a relative after a crossing) — it
  is NOT provided by anything in `vision/` today.**
- **SFace** face-ID **track-persistent**: identify a track ONCE, carry identity across the
  track (not per-utterance re-ID) — the key mitigation for SFace ~91% cross-pose accuracy ×
  only 3 enrolled frontal samples, which would otherwise false-reject the owner.
- **MediaPipe Face Landmarker** per tracked face → head-pose matrix (yaw/pitch → facing
  cone) + mouth/jaw blendshapes (jawOpen/mouthClose Δ over recent frames → lip-active).

**Owner-scoped ring (fixes the attribution defect):** publish a timestamped ring (~last 30 s
so onset queries survive long turns) where each sample is **owner-track-scoped**:
`{ts, owner_present, owner_facing, owner_lip_active}` (+ optional `other_lip_active` for
telemetry). The fusion's lip leg reads **owner_lip_active ONLY** — never primary/any face.
`ts` is **epoch `time.time()`** (define the clock domain; the agent converts its
`time.monotonic()` onset stamp to compare). Atomic write to `~/.jarvis/attention.json`.

**Privacy + webcam-tool contention (fixes the blocking regression):** frames stay
**in-memory** — no `person_tracker.jpg` on disk. BUT `vision/webcam.py:163-201,348-372` uses
that JPEG as the `webcam` tool's frame source AND its EBUSY fallback while the tracker holds
the camera — deleting it would make `webcam` + `face_recognition` (this design's own
enrollment path) go dark. **Resolution:** the voice-client serves the latest frame in-memory
over `:8767` (e.g. `GET /frame`, RAM-only, freshness-gated); teach `grab_jpeg` to try that
endpoint before opening the device. Opt-in `JARVIS_LOOK_TO_TALK=1` (default off); visible
"camera active" indicator.

### 2. Gate integration — agent-side onset snapshot
The gate fires at turn **completion**, but the question is "was the owner facing + lip-active
at speech **onset**." A 30 s ring *could* be queried at gate time, but the robust design is a
**snapshot at the VAD event**: on `user_state_changed → speaking` (jarvis_agent.py:6146,
which already stamps `session._jarvis_speech_started_at`), immediately read `attention.json`
and stash the onset verdict on the session **and** a module-global (so the *pure-text*
`_addressing_decision` / `_would_discard_transcript` mirrors — called from turn_rescue /
_bargein_veto without a session — can see it). Window `JARVIS_LTT_ONSET_WINDOW_S` (default
onset−0.3 s → onset+1.0 s). Caveats to document: `_jarvis_speech_started_at` is overwritten
per VAD segment (multi-segment turns query the last segment's onset); 5 Hz sampling is
marginal for sub-500 ms utterances.

Decision (additive; hard-accepts unchanged):
1. Vocative / wake / greeting → accept (unchanged).
2. Else if `JARVIS_LOOK_TO_TALK` live AND onset attention fresh (<2 s) AND
   **owner_facing + owner_lip_active** in the onset window AND directedness score ≥ threshold
   → **accept (no wake word)**.
3. Else → today's engagement-window / directedness behavior (fail-open).

### 3. Engagement indicator — flag file → `:8767/status` → tray
Write `~/.jarvis/.engaged` on the addressing decision → publish on `/status` → tray. Show
"locked onto you" vs "hearing the room (dropping)". **Frozen-tray caveats:** cyan already
means "listening — you speaking" (main.rs:216) — this needs an explicit state-machine
mapping in the sign-off, not just a shade; "no new color" vs a new dimmed-cyan tint must be
reconciled at sign-off. Define an `.engaged` **TTL/decay** so it doesn't display a stale
last-turn decision forever. Ships only with `npm run build` **and** `cargo build --release`.

### 4. Directedness classifier (#294) — fused, not after
PR #294's `pipeline/directedness.py` is the Phase-2 "is this a request" leg. Google never
trusted the visual phase alone. Merge dependency stated above.

## Install (exact — naive `pip install` corrupts the venv's cv2)

```
src/voice-agent/.venv/bin/pip install mediapipe==0.10.35 --no-deps
src/voice-agent/.venv/bin/pip install absl-py
```
`--no-deps` avoids `opencv-contrib-python 5.0.0.93` colliding with the pinned
`opencv-python-headless 4.13.0.92` (both own `cv2`). matplotlib (hard-imported by mediapipe),
pillow, flatbuffers, sounddevice, numpy are already present. No protobuf/jax needed. `pip
check` will permanently warn about the unsatisfied opencv-contrib-python metadata dep —
**accepted/cosmetic; document so tooling doesn't "fix" it destructively.** Net weight ~40 MB
+ the **3.7 MB `face_landmarker.task`** model → fetch into `~/.jarvis/models/` with a pinned
SHA (mirror `vision/face_id.py`'s model fetch).

## Data flow

```
webcam ─(V4L2, client-owned)─► attention_tracker (YuNet+track-assoc+SFace+MediaPipe, 5Hz, in-mem)
                                    │ owner-scoped ring, epoch ts
                                    ▼
                             ~/.jarvis/attention.json (fresh<2s)  + GET :8767/frame (RAM)
mic ─► VAD onset event ─► [agent snapshots attention.json → stash on session + module-global]
                                    │
                        STT final ─► on_user_turn_completed ─► _addressing_decision(text, onset_attention, #294)
                              ├─ accept → LLM reply          → ~/.jarvis/.engaged = you (TTL)
                              └─ drop (ambient)              → ~/.jarvis/.engaged = room
                                    │
                   :8767/status {engaged} ─► tray (mapped state, sign-off)
```

## Error handling / fail-safe

- Camera unavailable / dark / no owner-face / stale ring / model-load fail → attention signal
  absent → gate falls back to today's behavior. Gate is strictly additive-accept → cannot make
  JARVIS more deaf **at the decision level**.
- **System-level regressions to prevent:** (a) webcam-tool/face-ID blackout — resolved by the
  in-memory `GET /frame` handoff above (do NOT ship the loop without it); (b) mic-path
  starvation — resolved by the dedicated-thread + watchdog (or subprocess) isolation above.
- Per-frame CV exception → skip frame, keep last ring state, never raise into capture.
- All behind `JARVIS_LOOK_TO_TALK` (default off) + `JARVIS_LOOK_TO_TALK_LIVE` (default 0 = shadow).

## Performance (measured on the target box, i9-10885H)

Composed ~44–50 ms/frame (YuNet 11.5 + MediaPipe 17.5–23.2 + SFace 14.9 @ 640×480, 1 face);
~30–35 ms steady-state with track-persistent ID keeping SFace off the hot path. At 5 Hz ≈
15–25% of one of 16 logical cores — fine next to GPU Whisper (mediapipe inits GL on the Intel
iGPU, not the RTX 2060). **Scales per detected face** — a family room of 2–4 faces multiplies
the MediaPipe+SFace legs; budget for it.

## Rollout

- **Phase 0 — indicator** (master-only, low-risk): `.engaged` flag + tray state from the
  *existing* addressing decision. Immediate value, no camera.
- **Phase 1 — attention loop + shadow gate** (needs #286+#294 merged for full fusion): build
  `attention_tracker` (incl. the greenfield track-association), owner-scoped ring, onset
  snapshot, `GET /frame`. Log per-turn `{would_accept, owner_facing, owner_lip_active,
  directedness}` to telemetry. No gating.
- **Phase 2 — calibrate:** enroll more owner face samples at desktop distance/angle; tune the
  head-pose cone, dwell, onset window, lip threshold, freshness against real room data
  (FRR@fixed-FAR from shadow logs, incl. the two false-accept cases).
- **Phase 3 — flip live:** `JARVIS_LOOK_TO_TALK_LIVE=1`. Revert = env back to 0.

## Testing

- Unit: head-pose cone; **track association (birth/death/crossing/re-ID)**; track-persistent
  ID; owner-scoped lip-activity Δ; ring freshness + epoch clock conversion; onset snapshot +
  window; fused `_addressing_decision` (each leg + fail-open); shadow-vs-live.
- Integration: synthetic owner-scoped ring + onset stamp + transcript → decision matrix.
- Live (Ulrich, required — vision efficacy is physical), MUST include the family-room killers:
  (a) owner faces + speaks → accept; (b) owner faces + silent while **kid talks** → **drop**
  (the attribution test); (c) owner faces while **talking to the kid** → known false-accept,
  measure rate; (d) family talks, owner not facing → drop; (e) owner off-angle/dim → fail open,
  not silently dropped; (f) two faces both facing → identity stays on owner track.

## Env knobs

`JARVIS_LOOK_TO_TALK` · `JARVIS_LOOK_TO_TALK_LIVE` · `JARVIS_LTT_YAW_CONE_DEG` (~18) ·
`JARVIS_LTT_DWELL_S` (~0.7) · `JARVIS_LTT_ONSET_WINDOW_S` (−0.3/+1.0) · `JARVIS_LTT_FRESH_S`
(2.0) · existing `JARVIS_DIR_*` (#294).

## Honest limitations

- Fires only when the owner is **in view + facing** — no hands-free from across the room or
  facing away (fail-open leaves wake-word / facing the camera as the only path there).
- **Two residual false-accepts** (see Core principle): owner-facing-while-talking-to-someone,
  and owner-mouth-active-while-other-talks — only the text heuristic guards them; closing them
  needs audio-bound active-speaker detection (out of scope).
- Face-ID at distance/dim is the weakest leg; track-persistence + fail-open + more enrolled
  samples contain it, but per-room calibration is mandatory, not optional.
- The **face-track association** component is new and can mis-assign identity when faces cross.
- Bigger build than a config flip; phased so Phase 0 delivers immediately.
