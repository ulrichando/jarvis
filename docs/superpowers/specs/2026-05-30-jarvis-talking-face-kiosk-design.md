# JARVIS Talking Face — live Blender head, captured into the kiosk

- **Date:** 2026-05-30
- **Status:** Approved (design) — pending spec review → implementation plan
- **Owner:** Ulrich
- **Topic:** Replace the kiosk WebGL "ring" with a live, lip-flapping 3D face driven by JARVIS's voice.

---

## 1. Problem & intent

The kiosk (`?route=kiosk`) currently shows a WebGL shader "ring"
([`AgentAudioVisualizerAura`](../../../src/desktop-tauri/src/components/agents-ui/agent-audio-visualizer-aura.tsx))
that pulses with JARVIS's voice. Ulrich wants JARVIS to have a **face**: a 3D head
that opens its mouth when JARVIS speaks, shown in the kiosk in place of the ring.

Three product decisions were made during brainstorming and are **fixed** for this spec:

| Decision | Choice | Consequence |
|---|---|---|
| **Render target** | **Blender renders live, captured into the kiosk** | Kiosk depends on a running Blender instance; heavier/more fragile than in-kiosk WebGL, mitigated by a ring fallback. |
| **The head** | **Talk-ready ARKit / FaceCap head** (Sketchfab uid `29c2a506582a4157bf970bb8721a970c`) | Ships with the 52 ARKit blendshapes; talks with zero sculpting. The earlier-imported `female-head-sculpt` is NOT used (no rig, no mouth interior, 274k verts). |
| **Mouth motion (MVP)** | **Jaw-open driven by voice loudness** | Reads as talking, not phoneme-perfect lip-sync. Upgrade hooks left for visemes / full ARKit. |

Two internal forks were resolved in favor of the recommended option:

- **Capture mechanism:** MJPEG-over-HTTP frame stream from a Blender offscreen render (not LiveKit video, not OS window placement).
- **Loudness source:** PipeWire monitor tap in the sidecar (not a PCM tap inside voice-agent core).

### Why this is feasible (verified)

- The FaceCap head asset **exists** and is a real ARKit scan head (preview confirmed 2026-05-30).
- A working prototype already exists:
  [`src/voice-agent/animators/blender_face.py`](../../../src/voice-agent/animators/blender_face.py)
  drives `FaceCap_Head` shape keys (jawOpen + supporting) from JARVIS's
  `[tts] Orpheus rendered` log events over the Blender MCP socket (`:9876`). This spec
  **evolves** that file rather than starting from scratch.
- Blender's MCP addon is already running and reachable (`localhost:9876`).

---

## 2. Goals / non-goals

**Goals (MVP):**
1. A FaceCap head, framed and lit, talks in Blender when JARVIS speaks, with jaw openness
   proportional to **real voice loudness**, returning to neutral on silence/interruption.
2. The Blender face viewport is streamed into the kiosk and **replaces the ring**, in the
   same 448×448 slot.
3. If the face stream is unavailable (Blender down, stream stalled), the kiosk **falls back
   to the existing ring** — it never goes blank.
4. No regressions to the existing kiosk, voice-agent core, or the `blender_face.py` prototype's
   reconnect behavior.

**Non-goals (explicitly deferred):**
- Viseme lip-sync (phoneme-accurate mouth shapes).
- Full ARKit expression (brows, blinks-as-emotion, gaze, head turns as semantic signals).
- Rigging the `female-head-sculpt`.
- Remote / multi-machine kiosk (would favor the LiveKit-video capture alt).
- Any `src/cli/` changes.

---

## 3. Architecture

```
 JARVIS TTS audio
   │
   ├─►  PipeWire monitor of JARVIS output sink ──► RMS loudness (0..1, ~60 Hz)
   │
   └─►  voice-agent.log  [tts] Orpheus rendered / data-stop ──► speaking gate (on/off)
                                   │
                                   ▼
                    Face Animator  (Python sidecar, evolves blender_face.py)
                      target_jaw = gate ? f(loudness) : 0
                      smoothed (attack 0.20 / decay 0.12) + co-articulation
                                   │  MCP socket :9876  (execute_code → shape_keys)
                                   ▼
                    Blender:  FaceCap_Head blendshapes move
                      camera "JarvisFaceCam" frames the face
                                   │
                      bpy.app.timers tick (main thread):
                        GPUOffScreen.draw_view3d → pixels → JPEG → latest_frame
                                   │
                      HTTP thread serves multipart/x-mixed-replace
                                   │  http://127.0.0.1:8770/stream.mjpg
                                   ▼
                    Kiosk  <FaceStream>  (replaces <AgentAudioVisualizerAura>)
                      <img src=".../stream.mjpg">  in the 448×448 slot
                      health-watch → on error/stall, render the ring instead
```

**Three units, each independently testable:**

### Component A — Blender scene (the renderer)

- **Setup script** (idempotent, run via MCP `execute_blender_code`):
  - Import FaceCap head; rename its mesh object to `FaceCap_Head` (the name the animator expects).
  - **Assert** the 52 ARKit shape keys exist (fail loudly with the list if not). Record the
    actual `key_blocks` index→name map; the animator's ARKit indices (`JAW_OPEN=17`, etc.) are
    re-validated against it rather than trusted blindly.
  - Add camera `JarvisFaceCam` framing the face (head-and-shoulders, slight 3/4 or frontal —
    final framing tuned against a screenshot).
  - Add portrait lighting: key + fill + a **cyan rim** (`#1FD5F9`) to echo the kiosk palette;
    dark world background (near-black) so it composites cleanly over the kiosk's black backdrop.
  - Neutral idle pose. (Optional MVP+: subtle blink + micro head-sway via the animator so the
    face isn't a frozen mask between utterances.)
  - Keep the `female-head-sculpt` objects in the file but **hidden** (`hide_render`,
    `hide_viewport`) — not deleted (user-imported asset).
- **What it depends on:** Blender + MCP addon running; the FaceCap asset downloadable.

### Component B — Face Animator (Python sidecar)

Evolves `src/voice-agent/animators/blender_face.py`. Keeps its `BlenderConnection`,
`SpeechTracker` (log tail → speaking gate), reconnect, and neutral-on-exit behavior.
**Changes:**

- **New: loudness source.** A `LoudnessMonitor` that reads JARVIS's output audio and emits a
  smoothed RMS level in `0..1`.
  - Implementation: subprocess capture of the **PipeWire monitor** of JARVIS's output sink
    (`pw-cat --record` / `parec` on the `.monitor` source), reading small frames (~10–20 ms),
    computing RMS, normalized with a configurable gain/floor.
  - Sink selection: env `JARVIS_FACE_AUDIO_MONITOR` overrides; otherwise autodetect the
    JARVIS echo-cancel / output sink monitor (the voice I/O is pinned to PipeWire echo-cancel
    virtual devices — see memory `project_jarvis_audio_echocancel_routing`). Fall back to the
    default sink monitor.
  - Degradation: if no monitor can be opened, `LoudnessMonitor` reports a constant "speaking
    level" so the face still flaps (reverting to prototype behavior) — never silent-fails to a
    dead jaw.
- **Changed: jaw mapping.** While the **gate is on**, `target_jaw = clamp(gain * rms)`; while
  off, `target_jaw = 0`. Smooth with the existing attack/decay. Co-articulation unchanged
  (`mouthClose` inverse, slight `funnel`/`pucker`). On `data-stop`, gate→off, jaw decays to 0.
  - The gate prevents non-JARVIS system audio (during JARVIS's own silence) from moving the jaw;
    during JARVIS speech the monitor is dominated by JARVIS's voice, so RMS≈JARVIS loudness.
- **Unchanged:** ~30 fps send throttle, change-threshold (`>0.003`) to avoid redundant socket
  writes, MCP `execute_code → set_shape_keys`.
- **What it depends on:** voice-agent log path; PipeWire; the Blender MCP socket.

### Component C — Capture → kiosk

- **Blender-side frame server** (installed via `execute_blender_code`, self-contained module
  scheduled with `bpy.app.timers`):
  - On each tick (main thread, required for GPU): render `JarvisFaceCam` to a
    `gpu.types.GPUOffScreen` at 448×448 (configurable), read pixels, JPEG-encode (Pillow if
    available, else Blender image save to an in-memory path), store as `latest_frame: bytes`.
  - A daemon **HTTP thread** serves `GET /stream.mjpg` as `multipart/x-mixed-replace` from
    `latest_frame`, and `GET /frame.jpg` (single frame, for smoke tests / health) on
    `127.0.0.1:8770`. The HTTP thread only reads the shared bytes; all GPU work stays on the
    timer/main thread.
  - Target 30 fps; drop to 24 fps / 384² if GPU-bound (env-tunable).
- **Kiosk-side** (`src/desktop-tauri`):
  - New `FaceStream` component rendering `<img src="http://127.0.0.1:8770/stream.mjpg">` sized to
    the existing `AURA_SIZE` (448) slot in
    [`KioskHUD.jsx`](../../../src/desktop-tauri/src/components/KioskHUD.jsx).
  - **Fallback:** watch `<img>` `onerror` + a frame-staleness timeout; on failure render the
    existing `<AgentAudioVisualizerAura>` instead. A flag (`VITE_JARVIS_FACE_KIOSK`, default on)
    can force ring-only.
  - The existing `/status` poll + `agentState` stay — they drive the ring fallback and may add a
    CSS "speaking" glow around the face frame.
  - **Tauri CSP** (`src-tauri/tauri.conf.json`): add `http://127.0.0.1:8770` to **`img-src`**
    (and `connect-src` if a WS health-ping is added). Current `img-src` is
    `'self' data: blob: asset:` — append the origin.
- **What it depends on:** the Blender frame server; the CSP allowance; `cargo build --release`
  to re-embed `dist/` for the installed kiosk binary.

---

## 4. Data flow & interfaces

- **Speaking gate:** `SpeechTracker.is_speaking()` (log-derived) — unchanged contract.
- **Loudness:** `LoudnessMonitor.level() -> float in [0,1]` — new, polled each animation tick.
- **Blender shape-key write:** `BlenderConnection.set_shape_keys({index: value})` over MCP
  `execute_code` — unchanged contract; target object `FaceCap_Head`.
- **Frame stream:** HTTP `multipart/x-mixed-replace; boundary=frame`, JPEG parts, on
  `127.0.0.1:8770/stream.mjpg`. Single-frame health: `127.0.0.1:8770/frame.jpg`.
- **Kiosk consumption:** native `<img>` element (MJPEG auto-refresh). No JS decode loop needed.

---

## 5. Failure handling

| Failure | Behavior |
|---|---|
| Blender not running / MCP socket dead | Animator logs + retries (existing reconnect). No frames → kiosk `<img>` errors → **ring fallback**. |
| Frame server stalls (no new frame) | Kiosk staleness timeout → **ring fallback**; recovers automatically when frames resume. |
| PipeWire monitor unavailable | `LoudnessMonitor` reports constant speaking level → face still flaps (prototype behavior). |
| Interruption (`data-stop`) mid-speech | Gate off → jaw decays to neutral. |
| GPU pressure | Env-tunable fps/resolution drop; offscreen render is a single head (cheap). |
| Animator killed | `finally`: shape keys reset to NEUTRAL, socket closed (existing behavior). |

---

## 6. Performance budget

- Frame: 448², JPEG q≈70 ≈ 20–40 KB; 30 fps over localhost ≈ ~1 MB/s — trivial.
- Offscreen render of one head: sub-millisecond-to-low-ms on a desktop GPU.
- Animator socket writes throttled to ~30 fps and gated on `>0.003` change.
- Loudness capture: ~10–20 ms frames, RMS only — negligible CPU.

---

## 7. Testing strategy

- **Animator unit tests** (voice-agent pytest, no Blender/PipeWire needed — inject fakes):
  - loudness→jaw mapping (gain, clamp), attack/decay smoothing curve, gate on/off transitions,
    `data-stop` → decay, monitor-unavailable degradation path.
- **Blender frame server smoke test:** after install, `GET /frame.jpg` returns a valid JPEG of
  the expected dimensions (run via MCP / curl).
- **Kiosk build:** `npm run build` (catches syntax/import); manual `?route=kiosk`:
  - face appears in the ring's slot;
  - kill the frame server → confirm **ring fallback**;
  - restart → confirm face returns.
- **End-to-end:** speak to JARVIS → mouth tracks loudness; go silent → neutral; barge-in →
  immediate decay.
- **Verification gates (per `.claude/rules/regression-prevention.md`):** voice-agent edits →
  pytest green; desktop edits → `npm run build`; installed kiosk → `cargo build --release`.
  Do not restart `jarvis-voice-agent.service` within 60 s of the last turn (check
  `turn_telemetry.db`).

---

## 8. Scope

```
SCOPE:
  src/voice-agent/animators/blender_face.py        (evolve: loudness, gate, mapping)
  src/voice-agent/animators/loudness_monitor.py    (new: PipeWire RMS)
  src/voice-agent/animators/blender_frame_server.py (new: offscreen render + MJPEG; installed into Blender)
  src/voice-agent/tests/test_face_animator.py       (new: unit tests)
  src/desktop-tauri/src/components/KioskHUD.jsx      (swap ring → FaceStream + fallback)
  src/desktop-tauri/src/components/FaceStream.jsx    (new)
  src/desktop-tauri/src-tauri/tauri.conf.json        (CSP img-src += :8770)
  bin/jarvis-face-animator (or similar)              (launcher, optional)

OUT:
  src/voice-agent/jarvis_agent.py and voice-agent core (no PCM tap)
  src/desktop-tauri/src/components/agents-ui/* (ring shader left intact for fallback)
  src/cli/**
  female-head-sculpt rig
  viseme / full-ARKit motion

WHY OUT:
  Voice core is load-bearing (4 monkey-patches, confab detector, STT/TTS chains) — the
  sidecar + PipeWire monitor get loudness without touching it. The ring must remain fully
  functional as the fallback renderer. CLI is a separate codebase.
```

---

## 9. Risks & open items

- **R1 — ARKit index drift.** The animator hardcodes `JAW_OPEN=17` etc. The FaceCap head's
  `key_blocks` order is verified on setup; if it differs, map by **shape-key name** (`jawOpen`,
  `mouthClose`, `mouthFunnel`, `mouthPucker`) instead of index. *Mitigation: name-based lookup
  with index as a hint.*
- **R2 — Blender-as-renderer fragility (accepted).** Kiosk now needs Blender + MCP up. Ring
  fallback prevents blank screen, but "face works" requires an extra long-running process. Noted
  and accepted by the user; revisit if it proves flaky → in-kiosk WebGL is the escape hatch.
- **R3 — Monitor picks up non-JARVIS audio.** Gating by speaking-state confines jaw motion to
  JARVIS's speech windows; acceptable for MVP.
- **R4 — Offscreen render on Blender's main thread** must not block the MCP addon. Timer-driven,
  cheap single-head render; if it contends, lower fps. *Validate during implementation.*
- **R5 — Aesthetic clash.** A realistic scan head over a holographic UI may look uncanny. Cyan
  rim light + dark world is the first pass; a stylized/hologram material is a fast follow if Ulrich
  dislikes the realism (does not change architecture).

---

## 10. Future upgrades (hooks, not built now)

- **Visemes:** swap loudness→jaw for phoneme estimation (browser `wawa-lipsync` if we ever move
  rendering in-kiosk, or a Python formant/viseme estimator feeding the same shape-key writer).
- **Full ARKit expression:** map JARVIS emotional/turn-router state (BANTER/REASONING/EMOTIONAL)
  to brows/eyes/head for an emoting face.
- **In-kiosk WebGL render:** export the rigged head to GLB and drive morph targets with
  three.js (already installed in desktop-tauri) — removes the Blender runtime dependency. The
  loudness→jaw contract carries over unchanged.
```
