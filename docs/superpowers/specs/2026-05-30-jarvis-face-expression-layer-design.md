# JARVIS Kiosk Face — Expression Layer Design

**Date:** 2026-05-30
**Branch:** feat/jarvis-talking-face
**Status:** design — awaiting user review before plan
**Builds on:** [2026-05-30-jarvis-face-viseme-lipsync-design.md](2026-05-30-jarvis-face-viseme-lipsync-design.md) (the shipped viseme lip-sync)

**Goal:** Make the talking face *expressive*, not just mouth-accurate — drive the brows, eyes, cheeks, and emotional mouth (smile/frown) that the FaceCap GLB already has but the viseme engine leaves idle. The face should react to *what JARVIS says* (sentiment + punctuation) and stay subtly alive between sentences. Plus a small size bump.

---

## Why

The GLB is a fully-rigged ARKit-52 head, but the viseme engine drives only ~15 morphs (mouth + blink). Everything above the mouth — brows, cheeks, eye-wide, smile/frown — sits at zero, so the face reads as a moving mouth on a still face. This is **pure software**: we drive morphs that already exist (no Blender, no re-rig, no GLB change). All local, CPU-only, riding the existing `/face` → kiosk pipeline.

## Locked decisions

1. **Driver = content + idle.** Expressions react to the reply's **sentiment** and **punctuation**, plus ambient **idle micro-expressions** for liveliness.
2. **Sentiment source = VADER** (`vaderSentiment`, pure-Python offline lexicon). Chosen because it natively boosts intensity on `!`, CAPS, and emphasis words — exactly our cue set — with no model/GPU.
3. **Home = the voice-client.** The expression engine runs alongside the viseme engine; both derive from the transcript the voice-client already has. **No voice-agent / turn-router changes** (kept clean — `jarvis_agent.py` is off-limits WIP).
4. **Merge, don't replace.** Viseme weights (mouth) and expression weights (brows/eyes/cheeks/smile-frown) touch *disjoint* morphs and are merged into one `/face` payload.
5. **Size bump:** kiosk `AURA_SIZE` 448 → 576 px.

**Out of scope (YAGNI):** turn-router emotion hookup (would cross into the voice-agent); per-word emphasis timing; gaze tracking; a richer/new model (the rig is already complete).

---

## Architecture

```
voice-client (already has the agent transcript + RMS):
   transcript text ──┬─▶ VisemeEngine   → mouth morphs {target_24, 28, …}   (existing)
                     └─▶ ExpressionEngine→ expr morphs  {target_0..4, 17/18, 20/21, 37-40} (NEW)
                              │ VADER compound + punctuation → preset blend
                              ▼
        playback loop: state.face_weights = { **viseme_weights, **expression_weights }
                              ▼
        GET /face  →  merged per-frame ARKit-morph weights
                              ▼
   kiosk (FaceWebGL): applies the WIDER morph set (mouth + brows/eyes/cheeks/smile-frown),
        each eased toward target. Idle micro-expressions (brow flicks, eye darts) layered
        on top of the existing blink + sway.
```

The two engines are **disjoint by morph**: visemes own `[24,28,29,36,43-51]` (jaw/lips); expression owns `[0,1,2,3,4,17,18,20,21,37,38,39,40]` (brows/eyeWide/cheeks/smile/frown). `mouthSmile` (37/38) is in the kiosk's mouth-iteration set but viseme poses never emit it, so expression owns it cleanly. The merge `{**viseme, **expression}` therefore has no real key collision.

### Components

**1. Expression tables — extend `lipsync/viseme_tables.py`**
Add the brow/cheek/frown ARKit names to `ARKIT_TO_TARGET` (canonical ARKit-52 indices, consistent with the verified jawOpen=24 / eyeWide=17-18 ordering):
- `browInnerUp`→`target_0`, `browDownLeft`→`target_1`, `browDownRight`→`target_2`, `browOuterUpLeft`→`target_3`, `browOuterUpRight`→`target_4`
- `cheekSquintLeft`→`target_20`, `cheekSquintRight`→`target_21`
- `mouthFrownLeft`→`target_39`, `mouthFrownRight`→`target_40`
- (`eyeWideLeft/Right` 17/18 and `mouthSmileLeft/Right` 37/38 already present)

Plus an `EXPRESSION_PRESETS` dict — each a small ARKit-name→weight pose at full intensity:
- `warm` → `{mouthSmileLeft:0.5, mouthSmileRight:0.5, cheekSquintLeft:0.3, cheekSquintRight:0.3, browInnerUp:0.15}`
- `serious` → `{browDownLeft:0.3, browDownRight:0.3, mouthFrownLeft:0.15, mouthFrownRight:0.15}`
- `inquisitive` → `{browInnerUp:0.4, browOuterUpLeft:0.4, browOuterUpRight:0.4, eyeWideLeft:0.2, eyeWideRight:0.2}`
- `emphatic` → `{browOuterUpLeft:0.5, browOuterUpRight:0.5, eyeWideLeft:0.35, eyeWideRight:0.35}`

**2. Expression engine — `lipsync/expression.py` (new)**
- `expression_for_text(text) -> dict[str, float]` — returns `{target_N: weight}` (already resolved to GLB keys via `ARKIT_TO_TARGET`, like `resolve_pose`).
  - VADER `compound` ∈ [-1,1]: `compound > 0.25` → blend `warm` × min(1, compound·1.5); `compound < -0.25` → blend `serious` × min(1, |compound|·1.5).
  - `'?' in text` → add `inquisitive`. `'!' in text` (or ≥2 consecutive CAPS words) → add `emphatic`.
  - Blend = max per morph across active presets, clamped [0,1]; empty/neutral → `{}`.
- `ExpressionEngine`: `set_pending_text(text)` computes + stores the target dict; `frame(speaking) -> dict` returns it while `speaking`, `{}` otherwise (kiosk eases out; idle micro-expressions fill the gap). Mirrors `VisemeEngine` for symmetry + testability. VADER analyzer loaded once at import (pure-Python, cheap).

**3. Voice-client merge — `jarvis_voice_client.py`**
A module-level `_expression_engine = ExpressionEngine()`. The transcript handler already calls `_viseme_engine.set_pending_text(...)`; add `_expression_engine.set_pending_text(...)` alongside. In the playback loop, after computing `viseme_weights`, merge: `state.face_weights = {**viseme_weights, **_expression_engine.frame(state.speaking)}`. Same try/except guard (expression failure → just visemes).

**4. Kiosk — `FaceWebGL.jsx`**
- Widen the per-frame applied morph set from `MOUTH` to `MOUTH ∪ EXPRESSION` where `EXPRESSION = [0,1,2,3,4,17,18,20,21,39,40]` (smile 37/38 already in MOUTH). Each eases toward `getWeights()[key] || 0` like the mouth.
- `eyeWide` (17/18) moves from the *static* useMemo pose into the dynamic loop with a **baseline 0.55** (so expression adds/subtracts around the existing wide-eyed look rather than flattening it): `target = clamp(0.55 + (weights[key]||0))` for 17/18 only; other expression morphs use plain `weights[key]||0`.
- **Idle micro-expressions:** extend the existing idle `useFrame` — every ~4–9 s a brief brow micro-raise (`target_0` blip ~0.2 over ~400 ms) and independent occasional eye-dart (small `target_17/18` flicker), randomized, suppressed while a content expression is active (i.e., when `getWeights()` has brow keys).

**5. Size — `KioskHUD.jsx`**
`AURA_SIZE` 448 → 576. (The face is centered by computed offsets from `vp`, so the larger box stays centered; verify it fits common kiosk resolutions with margin.)

---

## Data flow (one turn)

1. Agent speaks → transcript streams to the voice-client → both engines get `set_pending_text`.
2. `ExpressionEngine` computes the preset blend once (VADER + punctuation); holds it for the utterance.
3. Each playback frame: `face_weights = visemes ∪ expression`. `/face` serves the union.
4. Kiosk applies all morphs; idle micro-expressions run when no content expression is active.

## Error handling / degradation

- VADER/expression throws → expression dict `{}` → face shows visemes only (never worse than the current shipped face).
- Empty/neutral text → `{}` → neutral upper face + idle micro-expressions.
- Expression morphs absent from `/face` → kiosk eases them to 0 (or eyeWide to its 0.55 baseline).
- Merge is order-stable; disjoint morphs mean no viseme/expression fight.

## Testing

- **Unit (`expression`):** positive text → smile/cheek morphs present; negative → browDown/frown; `'?'` → browOuterUp raise; `'!'`/CAPS → eyeWide; neutral → `{}`; all weights in [0,1]; keys are `target_N`.
- **Unit (tables):** new `ARKIT_TO_TARGET` entries map to the correct canonical indices (browInnerUp→target_0, cheekSquintLeft→target_20, mouthFrownLeft→target_39).
- **Unit (merge):** `{**viseme, **expression}` preserves both, no lost mouth morphs.
- **Integration:** during a positive `/speak` (e.g. "That's wonderful news!"), `/face` carries smile + brow morphs alongside the visemes.
- **Visual:** the `?route=faceonly` browser check — confirm brows/cheeks move on emotive lines, neutral on flat ones, idle flickers between.
- **Regression:** full voice-agent pytest stays green; `/level` + viseme behavior unchanged.

## File structure

**New:** `src/voice-agent/lipsync/expression.py`, `src/voice-agent/tests/test_expression.py`
**Modified:** `lipsync/viseme_tables.py` (ARKIT_TO_TARGET + EXPRESSION_PRESETS), `lipsync/__init__.py` (export `ExpressionEngine`), `jarvis_voice_client.py` (engine + merge), `requirements.txt` (`vaderSentiment`), `FaceWebGL.jsx` (wider apply + idle micro-expr), `KioskHUD.jsx` (AURA_SIZE). `voice_client_http_api.py` `/face` unchanged (it already serves whatever `face_weights` holds).
**Untouched (OUT):** `jarvis_agent.py` + turn-router, the production-hardening WIP, `src/cli/`, the GLB/model (no Blender).
