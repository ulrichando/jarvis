# JARVIS Kiosk Face — Local Viseme Lip-Sync Design

**Date:** 2026-05-30
**Branch:** feat/jarvis-talking-face
**Status:** design — awaiting user review before plan
**Supersedes the lip-sync portion of:** [2026-05-30-jarvis-talking-face-kiosk-design.md](2026-05-30-jarvis-talking-face-kiosk-design.md) (which shipped amplitude `/level` jaw-bob)

**Goal:** Upgrade the kiosk's WebGL talking face from amplitude jaw-bob (one number → jaw opens) to **real visemes** (the mouth forms word shapes), running **100% locally on CPU/RAM**, by driving Oculus-viseme → ARKit-morph weights from the text JARVIS already knows plus the audio RMS it already computes.

---

## Why this design (and not A2F)

A 5-way feasibility sweep (2026-05-30) established that NVIDIA Audio2Face-3D — the obvious "best" lip-sync — **cannot run on this box** (RTX 2060 Max-Q, 6 GB, driver 550 / CUDA 12.4, Turing):

| Path | Verdict | Blocker |
|---|---|---|
| A2F-3D NIM v2.x | ✗ | ~9.15 GB VRAM (have 6, unfixable), driver R570+, CUDA 12.8+, no Turing profile |
| A2F-3D NIM 1.x | ✗ | NVAIE paid license, no Turing profile, driver 560+ |
| A2F open SDK (local) | ✗ today | CUDA 12.8 / TRT 10.13 / driver 570 floor; C++-only; outputs mesh not blendshapes |
| Cloud A2F | ✗ (policy) | audio leaves the box every turn (trial ToS, 30-day retention, WAN latency, rate-limited) — violates JARVIS's local/privacy posture |
| **Local viseme** | **✓** | none — CPU/RAM, ~10–50 ms, fully local |

System RAM (64 GB) cannot substitute for the 6 GB VRAM: GPU inference needs weights in VRAM, and PCIe paging would collapse a 30 fps face. But the chosen local-viseme path runs entirely in **CPU + RAM**, where 64 GB is luxurious headroom.

**The unfair advantage:** JARVIS holds **both the exact text it is speaking and the rendered TTS audio**. A2F (and OVR LipSync's engine) only get audio and must *guess* phonemes. Knowing the text lets us produce more accurate visemes with no neural net, no GPU, no license. We borrow the **Oculus 15-viseme vocabulary** and the MIT English phoneme tables from [TalkingHead](https://github.com/met4citizen/TalkingHead) — a three.js project that already does exactly this in our exact rendering stack.

A2F remains a clean **future** upgrade: the day there is an Ampere+ GPU with ≥12 GB, the NIM drops in and emits the *same* ARKit-52 channels into the *same* morph map built here. Nothing in this design is wasted by that path.

---

## Locked decisions

1. **Timing:** lightweight **RMS-gated text-timing** — advance through the viseme sequence by elapsed playback time, gate openness by the RMS envelope. No forced aligner (avoids gigabytes of models + latency; TalkingHead proves text+timing is sufficient for conversational lengths).
2. **Liveliness:** mouth visemes **plus idle life** — automatic eye-blinks on a randomized 3–6 s cadence and a subtle head sway, so the face is not frozen while listening.
3. **Vocabulary:** **Oculus 15-viseme set** (`sil PP FF TH DD kk CH SS nn RR aa E ih oh ou`), tables lifted from TalkingHead (MIT). More natural than ARKit-only mappings.
4. **Home of the logic:** the **voice-client** (Python) owns viseme resolution — it already receives the spoken text (`lk.transcription`) and computes the RMS. The kiosk stays a dumb renderer.
5. **Fallback:** the amplitude `/level` jaw path is preserved; the face is **never worse than today** if any new piece is unavailable.

**Out of scope (YAGNI):** audio-driven emotion (A2F's Audio2Emotion); forced alignment; the NeuroSync neural model; the A2F NIM; non-English (English first).

---

## Architecture

```
voice-agent: session.say(text)
        │  (LiveKit publishes audio track + lk.transcription text)
        ▼
voice-client (jarvis_voice_client.py) — already receives BOTH:
   • lk.transcription ──▶ TEXT of the current utterance
   • played PCM ────────▶ RMS envelope  (state.output_level, today's /level)
        │
        ▼
   lipsync/viseme_engine.py  (NEW)
     text ─▶ g2p (lightweight) ─▶ Oculus 15-viseme sequence w/ relative durations
     per ~16–33 ms frame: advance cursor by elapsed audio time;
       RMS gates how OPEN the current viseme is (sound→open, pause→close)
     resolve current viseme ─▶ ARKit-morph weights via lipsync/viseme_tables.py
        │
        ▼
   voice_client_http_api.py  ──▶  GET /face
     { "weights": { "target_24": 0.61, "target_31": 0.28, ... },
       "level": 0.17 }                       (CORS, polled ~30–60 fps)
        │
        ▼
kiosk (FaceWebGL.jsx / KioskHUD.jsx):
   poll /face → apply each weight to the named morph in useFrame (smoothed,
   fast-open / slow-close as today). Idle life (blinks + head sway) runs
   LOCALLY in the kiosk (no network). Fallback: if /face is absent/empty,
   use /level amplitude-jaw (unchanged behavior).
```

### Components

**1. Viseme engine — `src/voice-agent/lipsync/viseme_engine.py` (new)**
- *What:* converts an utterance's text + a live RMS signal into per-frame ARKit-morph weights.
- *Interface:* `VisemeEngine.start_utterance(text: str, t0: float)`; `VisemeEngine.frame(now: float, rms: float) -> dict[str, float]` (returns `{target_name: weight}` for the current frame); `VisemeEngine.reset()`.
- *Depends on:* `viseme_tables.py` only. Pure CPU, no torch/librosa/GPU. g2p via a small pure-Python dependency (e.g. `g2p_en`) or, if even that is too heavy, a grapheme→viseme heuristic for v1 (decided in the plan; engine interface is identical either way).
- *Timing:* each viseme carries a nominal duration; the cursor walks the sequence in **real playback time** (`now − t0`) against those per-viseme durations — **not** stretched to a total length (the streaming TTS duration is unknown ahead of time). If the sequence finishes while audio is still playing, hold the final shape; if audio ends first, the RMS gate closes the mouth. The smoothed RMS modulates openness throughout and relaxes the mouth toward `sil` on silence (RMS below the existing speech threshold).

**2. Viseme tables — `src/voice-agent/lipsync/viseme_tables.py` (new)**
- The Oculus phoneme→viseme map and a `VISEME_TO_ARKIT` dict: each of the 15 visemes → a small set of ARKit morph weights (jaw + lips: `jawOpen`, `mouthClose`, `mouthFunnel`, `mouthPucker`, `mouthPress*`, `mouthUpperUp*`, `mouthStretch*` …).
- `ARKIT_TO_TARGET`: the static ARKit-name → `target_N` index table for the FaceCap GLB (the GLB names morphs generically `target_0..51`; ARKit canonical index 24 = jawOpen, matching the empirically found `target_24`). Built once from the ARKit-52 canonical ordering.

**3. Transport — `voice_client_http_api.py` (modify)**
- Add `GET /face` returning `{weights, level}` with the existing `_CORS_HEADERS`. `/level` stays for backward-compat + fallback. `state.output_level` already exists; add `state.face_weights: dict` updated by the engine on the playback frame loop.

**4. Kiosk consumer — `FaceWebGL.jsx` + `KioskHUD.jsx` (modify)**
- `KioskHUD` polls `/face` (replacing/augmenting the `/level` poll); writes the weights dict to a ref off-React (no per-frame re-render, per the existing reactor-removed rule).
- `FaceWebGL`'s `Head` builds a `target_name → influence-index` lookup once from `morphTargetDictionary`, then in `useFrame` eases each polled weight toward its target (fast-open k≈0.4 / slow-close k≈0.25, as today). Unlisted morphs ease back to 0.
- If `/face` is unavailable/empty, fall back to driving only `target_24` from `level` (today's exact behavior).

**5. Idle life — kiosk-side, in `FaceWebGL.jsx`**
- Blink: every 3–6 s (randomized, varied by frame count — `Math.random` is fine in the kiosk), drive `eyeBlinkLeft/Right` (ARKit 9/10) through a quick close→open curve (~120 ms). Suppressed during a blink already in progress.
- Head sway: a small low-amplitude sinusoid on the head group's rotation (≈±1.5°, ~0.1 Hz) applied when not actively speaking; damped to neutral while speaking so it doesn't fight visemes.
- Both are purely local (no network), cheap, and independent of the lip-sync path.

---

## Data flow (normal turn)

1. Supervisor replies → `session.say(text)` in the voice-agent.
2. LiveKit streams the TTS audio track and the `lk.transcription` text to the voice-client.
3. Voice-client's transcription handler hands the text to `VisemeEngine.start_utterance`.
4. The playback frame loop (already computing RMS for `/level`) calls `VisemeEngine.frame(now, rms)` each frame and stores the result in `state.face_weights`.
5. Kiosk polls `/face`, applies the weights → the mouth forms the word shapes; idle life runs locally between/around utterances.

---

## Error handling / graceful degradation

- **No transcription text** (e.g. the `/speak` HTTP path, or transcription missing) → engine returns `{}`; kiosk falls back to RMS-only amplitude jaw. *Never worse than today.*
- **g2p / unknown word fails** → engine emits a generic open/close viseme gated by RMS for that span.
- **`/face` endpoint unreachable** → kiosk uses `/level` (existing coded fallback).
- **Timing drift on long utterances** → RMS gating keeps openness audio-synced even if the *shape* cursor drifts; acceptable for conversational lengths. (Forced alignment is the future tightening lever.)
- **Engine exception** → caught in the frame loop; `face_weights` cleared; fallback engages. The engine must never break audio playback.

---

## Testing

- **Unit (viseme_engine):** text "hello world" → expected Oculus-viseme sequence; a synthetic RMS envelope gates openness; all emitted weights are valid `target_N` names in [0,1]; silence → relaxes to `sil`.
- **Unit (viseme_tables):** `ARKIT_TO_TARGET['jawOpen'] == 'target_24'`; every viseme maps only to morphs the GLB actually has.
- **Integration:** during a `/speak`, `/face` returns well-formed non-empty weights with non-zero jaw on voiced frames and closed on silence.
- **Visual/manual:** the `?route=faceonly` dev route (already polls `/level`; extend to `/face`) shows word-shaped mouth motion during speech — verified by the same browser-screenshot method used to prove `/level` (silence=closed, peak=open).
- **Regression:** `cd src/voice-agent && .venv/bin/python -m pytest tests/` stays green; `/level` behavior unchanged so the fallback is intact.
- **Deploy verification:** `npm run build` + `cargo build --release`, SIGKILL-relaunch the desktop (per the talking-face deploy rule), enter kiosk, confirm visemes track speech.

---

## File structure

**New**
- `src/voice-agent/lipsync/__init__.py`
- `src/voice-agent/lipsync/viseme_engine.py`
- `src/voice-agent/lipsync/viseme_tables.py`
- `src/voice-agent/tests/test_viseme_engine.py`
- `src/voice-agent/tests/test_viseme_tables.py`

**Modified**
- `src/voice-agent/jarvis_voice_client.py` — feed transcription text → engine; call `engine.frame` in the playback loop; store `state.face_weights`.
- `src/voice-agent/voice_client_http_api.py` — add `state.face_weights` + `GET /face`.
- `src/desktop-tauri/src/components/KioskHUD.jsx` — poll `/face`, write weights ref.
- `src/desktop-tauri/src/components/FaceWebGL.jsx` — apply multi-morph weights in `useFrame`; idle blink + head sway; `/level`-only fallback.
- `src/desktop-tauri/src/App.jsx` — `FaceOnlyDev` polls `/face` (dev verification).

**Untouched (OUT):** `jarvis_agent.py` and the rest of the voice-agent brain (the engine is voice-client-only); the production-hardening WIP; `src/cli/` (run-only).
