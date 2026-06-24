# JARVIS Face — Expression Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the kiosk face expressive — drive the idle ARKit morphs (brows, eyes, cheeks, smile/frown) from the reply's sentiment + punctuation, merged over the existing visemes, plus idle micro-expressions and a size bump.

**Architecture:** A new `ExpressionEngine` (VADER sentiment + punctuation → ARKit expression-morph weights) runs in the voice-client alongside the viseme engine. Their outputs (disjoint morphs) merge into one `/face` payload. The kiosk widens its applied-morph set beyond the mouth and layers idle micro-expressions on the existing blink/sway.

**Tech Stack:** Python 3.13 (voice-agent `.venv`), `vaderSentiment` (pure-Python offline lexicon), aiohttp; three.js/@react-three/fiber kiosk. Tests: pytest; kiosk `npm run build`.

**Spec:** [docs/superpowers/specs/2026-05-30-jarvis-face-expression-layer-design.md](../specs/2026-05-30-jarvis-face-expression-layer-design.md)

**Verified anchors (live code):**
- `viseme_tables.py`: `ARKIT_TO_TARGET` dict + `resolve_pose()`. Canonical ARKit-52 order confirmed (jawOpen=24, eyeWide=17/18, eyeBlink=13/14) → brow/cheek/frown indices are trustworthy.
- `jarvis_voice_client.py`: engine singleton at L149-150 (`from lipsync import VisemeEngine` / `_viseme_engine = VisemeEngine()`); playback merge at L591-602 (`state.face_weights = _viseme_engine.frame(...)` in try/except); transcript handler `_drain_text_stream` calls `_viseme_engine.set_pending_text("".join(buf))` per chunk when `is_agent`.
- `FaceWebGL.jsx`: module const `MOUTH = [24,28,29,36,37,38,43,44,45,46,47,48,49,50,51]`; single `useFrame((_, delta) => {...})` with the mouth loop + idle (blink 13/14, sway); static eyeWide 17/18=0.55 in `useMemo`.
- `KioskHUD.jsx`: `const AURA_SIZE = 448`.

---

## Task 1: Expression tables (extend `viseme_tables.py`)

**Files:**
- Modify: `src/voice-agent/lipsync/viseme_tables.py`
- Test: `src/voice-agent/tests/test_viseme_tables.py`

- [ ] **Step 1: Write the failing tests** — append to `src/voice-agent/tests/test_viseme_tables.py`:
```python
def test_expression_arkit_mappings():
    assert vt.ARKIT_TO_TARGET["browInnerUp"] == "target_0"
    assert vt.ARKIT_TO_TARGET["browDownLeft"] == "target_1"
    assert vt.ARKIT_TO_TARGET["browDownRight"] == "target_2"
    assert vt.ARKIT_TO_TARGET["browOuterUpLeft"] == "target_3"
    assert vt.ARKIT_TO_TARGET["browOuterUpRight"] == "target_4"
    assert vt.ARKIT_TO_TARGET["cheekSquintLeft"] == "target_20"
    assert vt.ARKIT_TO_TARGET["mouthFrownLeft"] == "target_39"


def test_expression_presets_valid():
    assert set(vt.EXPRESSION_PRESETS) == {"warm", "serious", "inquisitive", "emphatic"}
    for name, pose in vt.EXPRESSION_PRESETS.items():
        for morph, w in pose.items():
            assert morph in vt.ARKIT_TO_TARGET, f"{name}:{morph} not mapped"
            assert 0.0 <= w <= 1.0
```

- [ ] **Step 2: Run, verify FAIL** — `cd src/voice-agent && .venv/bin/python -m pytest tests/test_viseme_tables.py -q` → AttributeError (`EXPRESSION_PRESETS` / new keys missing).

- [ ] **Step 3: Extend the tables.** In `src/voice-agent/lipsync/viseme_tables.py`, add the new entries to `ARKIT_TO_TARGET` (insert before its closing `}`):
```python
    # expression layer — canonical ARKit-52 indices (brows / cheeks / frown)
    "browInnerUp":       "target_0",
    "browDownLeft":      "target_1",
    "browDownRight":     "target_2",
    "browOuterUpLeft":   "target_3",
    "browOuterUpRight":  "target_4",
    "cheekSquintLeft":   "target_20",
    "cheekSquintRight":  "target_21",
    "mouthFrownLeft":    "target_39",
    "mouthFrownRight":   "target_40",
```
Then add `EXPRESSION_PRESETS` right after the `VISEME_TO_ARKIT` dict:
```python
# Expression presets — each a brow/eye/cheek/mouth pose at full intensity
# (ARKit names, weights 0..1). The ExpressionEngine blends these from VADER
# sentiment + punctuation. Disjoint from the viseme mouth morphs by design.
EXPRESSION_PRESETS = {
    "warm": {
        "mouthSmileLeft": 0.5, "mouthSmileRight": 0.5,
        "cheekSquintLeft": 0.3, "cheekSquintRight": 0.3,
        "browInnerUp": 0.15,
    },
    "serious": {
        "browDownLeft": 0.3, "browDownRight": 0.3,
        "mouthFrownLeft": 0.15, "mouthFrownRight": 0.15,
    },
    "inquisitive": {
        "browInnerUp": 0.4, "browOuterUpLeft": 0.4, "browOuterUpRight": 0.4,
        "eyeWideLeft": 0.2, "eyeWideRight": 0.2,
    },
    "emphatic": {
        "browOuterUpLeft": 0.5, "browOuterUpRight": 0.5,
        "eyeWideLeft": 0.35, "eyeWideRight": 0.35,
    },
}
```

- [ ] **Step 4: Run, verify PASS** — `cd src/voice-agent && .venv/bin/python -m pytest tests/test_viseme_tables.py -q` → all pass.

- [ ] **Step 5: Commit**
```bash
git add src/voice-agent/lipsync/viseme_tables.py src/voice-agent/tests/test_viseme_tables.py
git commit -m "feat(expression): ARKit brow/cheek/frown morph map + expression presets"
```

---

## Task 2: ExpressionEngine (`lipsync/expression.py`)

**Files:**
- Create: `src/voice-agent/lipsync/expression.py`
- Modify: `src/voice-agent/lipsync/__init__.py` (export), `src/voice-agent/requirements.txt` (`vaderSentiment`)
- Test: `src/voice-agent/tests/test_expression.py`

- [ ] **Step 1: Add + install the dependency.** Append to `src/voice-agent/requirements.txt`:
```
vaderSentiment~=3.3  # offline lexicon sentiment for the face expression layer (no model/torch)
```
Run: `cd src/voice-agent && .venv/bin/pip install vaderSentiment` → expect `Successfully installed vaderSentiment-3.3.2`.

- [ ] **Step 2: Write the failing tests** — `src/voice-agent/tests/test_expression.py`:
```python
from lipsync.expression import expression_for_text, ExpressionEngine


def test_positive_text_smiles():
    w = expression_for_text("That is wonderful, I am so happy for you!")
    assert w.get("target_37", 0) > 0 or w.get("target_38", 0) > 0   # mouthSmile
    assert all(0.0 <= v <= 1.0 for v in w.values())
    assert all(k.startswith("target_") for k in w)


def test_negative_text_furrows_brows():
    w = expression_for_text("This is terrible, broken, and awful.")
    assert w.get("target_1", 0) > 0 or w.get("target_2", 0) > 0      # browDown


def test_question_raises_outer_brows():
    w = expression_for_text("Are you absolutely sure about that?")
    assert w.get("target_3", 0) > 0 or w.get("target_4", 0) > 0      # browOuterUp


def test_exclamation_widens_eyes():
    w = expression_for_text("Look out!")
    assert w.get("target_17", 0) > 0 or w.get("target_18", 0) > 0    # eyeWide


def test_neutral_statement_is_empty():
    assert expression_for_text("the file is in that folder") == {}


def test_empty_text_is_empty():
    assert expression_for_text("") == {}
    assert expression_for_text("   ") == {}


def test_engine_holds_while_speaking_clears_idle():
    eng = ExpressionEngine()
    eng.set_pending_text("Fantastic, brilliant work!")
    assert eng.frame(speaking=True)              # non-empty while speaking
    assert eng.frame(speaking=False) == {}       # cleared when idle
```

- [ ] **Step 3: Run, verify FAIL** — `cd src/voice-agent && .venv/bin/python -m pytest tests/test_expression.py -q` → ModuleNotFoundError `lipsync.expression`.

- [ ] **Step 4: Implement** `src/voice-agent/lipsync/expression.py`:
```python
"""Text -> facial-expression ARKit-morph weights for the kiosk face.

Drives the brows / eyes / cheeks / smile-frown that the viseme engine leaves
idle, from the reply's sentiment + punctuation. Pure CPU, offline (VADER is a
lexicon). One instance shared with the playback loop, mirroring VisemeEngine.
"""
from __future__ import annotations

from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from .viseme_tables import ARKIT_TO_TARGET, EXPRESSION_PRESETS

_VADER = SentimentIntensityAnalyzer()   # pure-Python lexicon, loaded once


def _blend(active: list[tuple[str, float]]) -> dict[str, float]:
    """Max-blend (preset_name, intensity) pairs into {target_N: weight}."""
    acc: dict[str, float] = {}
    for preset, intensity in active:
        scale = max(0.0, min(1.0, intensity))
        for morph, w in EXPRESSION_PRESETS[preset].items():
            tgt = ARKIT_TO_TARGET[morph]
            acc[tgt] = max(acc.get(tgt, 0.0), round(w * scale, 4))
    return acc


def expression_for_text(text: str) -> dict[str, float]:
    """Map `text` to a blend of expression presets -> {target_N: 0..1}."""
    t = (text or "").strip()
    if not t:
        return {}
    active: list[tuple[str, float]] = []
    compound = _VADER.polarity_scores(t)["compound"]
    if compound > 0.25:
        active.append(("warm", min(1.0, compound * 1.5)))
    elif compound < -0.25:
        active.append(("serious", min(1.0, abs(compound) * 1.5)))
    if "?" in t:
        active.append(("inquisitive", 1.0))
    caps = sum(1 for w in t.split() if len(w) >= 2 and w.isupper())
    if "!" in t or caps >= 2:
        active.append(("emphatic", 1.0))
    return _blend(active)


class ExpressionEngine:
    """Holds the current utterance's expression; emits it each frame while the
    agent is speaking (the kiosk eases it in/out)."""

    def __init__(self) -> None:
        self._expr: dict[str, float] = {}

    def set_pending_text(self, text: str) -> None:
        self._expr = expression_for_text(text)

    def reset(self) -> None:
        self._expr = {}

    def frame(self, speaking: bool) -> dict[str, float]:
        return dict(self._expr) if speaking else {}
```

- [ ] **Step 5: Export from the package.** Update `src/voice-agent/lipsync/__init__.py` to:
```python
"""Local viseme lip-sync for the kiosk face.

Turns JARVIS's known TTS text + audio RMS into ARKit-morph weights that
drive the FaceCap GLB. Pure CPU/RAM — no GPU, no neural net. See
docs/superpowers/specs/2026-05-30-jarvis-face-viseme-lipsync-design.md.
"""
from .viseme_engine import VisemeEngine
from .expression import ExpressionEngine

__all__ = ["VisemeEngine", "ExpressionEngine"]
```

- [ ] **Step 6: Run, verify PASS + package import** — `cd src/voice-agent && .venv/bin/python -m pytest tests/test_expression.py -q` (7 passed) and `.venv/bin/python -c "from lipsync import ExpressionEngine; print('ok')"` → `ok`.

- [ ] **Step 7: Commit**
```bash
git add src/voice-agent/lipsync/expression.py src/voice-agent/lipsync/__init__.py src/voice-agent/tests/test_expression.py src/voice-agent/requirements.txt
git commit -m "feat(expression): VADER+punctuation ExpressionEngine -> ARKit expression morphs"
```

---

## Task 3: Wire ExpressionEngine into the voice-client

**Files:**
- Modify: `src/voice-agent/jarvis_voice_client.py`

- [ ] **Step 1: Add the expression singleton.** At L149-150, change the import + add the singleton:
```python
from lipsync import VisemeEngine, ExpressionEngine
_viseme_engine = VisemeEngine()
_expression_engine = ExpressionEngine()
```

- [ ] **Step 2: Feed the transcript to both engines.** In `_drain_text_stream`, where it currently does `_viseme_engine.set_pending_text("".join(buf))`, set both from one joined string:
```python
            async for chunk in reader:
                buf.append(chunk)
                if is_agent:
                    _txt = "".join(buf)
                    _viseme_engine.set_pending_text(_txt)
                    _expression_engine.set_pending_text(_txt)
```

- [ ] **Step 3: Merge expression into face_weights.** Replace the playback-loop block at L591-602 with:
```python
            # Face morphs: viseme mouth shapes (text + RMS) merged with the
            # expression layer (brows/eyes/cheeks/smile-frown from sentiment).
            # Disjoint morphs, so the union is clean. Never raises into audio.
            try:
                _vw = _viseme_engine.frame(
                    now=time.monotonic(),
                    speaking=state.speaking,
                    rms=state.output_level,
                )
                state.face_weights = {**_vw, **_expression_engine.frame(state.speaking)}
            except Exception as e:
                log.debug(f"[face] frame failed: {e}")
                state.face_weights = {}
```

- [ ] **Step 4: Verify** — `cd src/voice-agent && .venv/bin/python -c "import jarvis_voice_client; print('import ok')"` (→ `import ok`) and `.venv/bin/python -m pytest tests/ -q` (full suite, 0 failures).

- [ ] **Step 5: Commit**
```bash
git add src/voice-agent/jarvis_voice_client.py
git commit -m "feat(expression): merge expression morphs into /face alongside visemes"
```

---

## Task 4: Kiosk — apply expression morphs + idle micro-expressions

**Files:**
- Modify: `src/voice-agent/desktop-tauri/src/components/FaceWebGL.jsx`

- [ ] **Step 1: Add the expression morph set (module const).** After the `MOUTH` const (L20), add:
```javascript
// Expression morphs (brows / cheeks / frown) driven each frame from /face.
// eyeWide (17/18) is handled separately with a 0.55 baseline so the wide-eyed
// look persists and expressions modulate around it. Module-level (no realloc).
const EXPRESSION = [0, 1, 2, 3, 4, 20, 21, 39, 40]
```

- [ ] **Step 2: Add idle-expression refs.** In `Head`, after `clockRef` (L28), add:
```javascript
  const browRef = useRef({ next: 5.0, t: -1 })   // idle brow flick
  const dartRef = useRef({ next: 4.0, t: -1, dir: 1 })  // idle eye dart
```

- [ ] **Step 3: Apply expression morphs + eyeWide baseline + idle micro-expressions.** In the single `useFrame`, immediately AFTER the existing `for (const n of MOUTH) {...}` loop and BEFORE the `// ── idle life ──` comment, insert:
```javascript
    // Expression morphs (brows/cheeks/frown) — ease gently toward /face value.
    for (const n of EXPRESSION) {
      const key = 'target_' + n
      const i = dict[key]
      if (i == null) continue
      const target = Math.max(0, Math.min(1, targets[key] || 0))
      const cur = inf[i] || 0
      inf[i] = cur + (target - cur) * 0.2
    }
    // eyeWide: 0.55 baseline + expression modulation (blink overrides below).
    const eyeTarget = Math.max(0, Math.min(1, 0.55 + (targets['target_17'] || 0)))
    const ewL = dict['target_17'], ewR = dict['target_18']
    if (ewL != null) inf[ewL] = inf[ewL] + (eyeTarget - inf[ewL]) * 0.2
    if (ewR != null) inf[ewR] = inf[ewR] + (eyeTarget - inf[ewR]) * 0.2
```
Then, INSIDE the `// ── idle life ──` section, after the head-sway block (after the `if (g) {...}` closing brace, before the `useFrame` callback's closing `})`), add the idle micro-expressions:
```javascript
    // Idle brow flick — only when no content expression is driving the brows.
    const hasExprBrow = ((targets['target_0'] || 0) + (targets['target_3'] || 0) + (targets['target_1'] || 0)) > 0.01
    const ib = dict['target_0']
    const brow = browRef.current
    if (!hasExprBrow && ib != null) {
      if (brow.t < 0 && now >= brow.next) { brow.t = now }
      if (brow.t >= 0) {
        const p = (now - brow.t) / 0.4
        if (p >= 1) { brow.t = -1; brow.next = now + 4 + Math.random() * 5 }
        else inf[ib] = Math.max(inf[ib], Math.sin(p * Math.PI) * 0.18)
      }
    }
    // Idle eye dart — brief subtle horizontal glance every ~4–8 s.
    const dart = dartRef.current
    if (dart.t < 0 && now >= dart.next) { dart.t = now; dart.dir = Math.random() < 0.5 ? -1 : 1 }
    let gaze = 0
    if (dart.t >= 0) {
      const p = (now - dart.t) / 0.5
      if (p >= 1) { dart.t = -1; dart.next = now + 4 + Math.random() * 4 }
      else gaze = Math.sin(p * Math.PI) * 0.25 * dart.dir
    }
    const lo = dict['target_11'], ro = dict['target_12']  // eyeLookOutLeft/Right
    const li = dict['target_9'],  ri = dict['target_10']  // eyeLookInLeft/Right
    if (lo != null) inf[lo] = Math.max(0, -gaze)
    if (ro != null) inf[ro] = Math.max(0,  gaze)
    if (li != null) inf[li] = Math.max(0,  gaze)
    if (ri != null) inf[ri] = Math.max(0, -gaze)
```

- [ ] **Step 4: Remove the now-dynamic eyeWide from the static `useMemo`** (it's driven in `useFrame` now). In the `useMemo`, delete these four lines:
```javascript
        const eL = o.morphTargetDictionary['target_17']
        const eR = o.morphTargetDictionary['target_18']
        if (inf && eL != null) inf[eL] = 0.55
        if (inf && eR != null) inf[eR] = 0.55
```
(The `useFrame` eyeWide easing from 0 toward the 0.55 baseline replaces the static set; the eyes settle to wide within ~1 s of first render.)

- [ ] **Step 5: Build** — `cd src/voice-agent/desktop-tauri && npm run build 2>&1 | grep -iE "built in|error"` → `✓ built`, no errors. Confirm exactly ONE `useFrame(` remains: `grep -c "useFrame(" src/voice-agent/desktop-tauri/src/components/FaceWebGL.jsx` → `1`.

- [ ] **Step 6: Commit**
```bash
git add src/voice-agent/desktop-tauri/src/components/FaceWebGL.jsx
git commit -m "feat(expression): kiosk applies brow/cheek/frown + eyeWide baseline + idle brow-flick/eye-dart"
```

---

## Task 5: Size bump + live verify + deploy

**Files:**
- Modify: `src/voice-agent/desktop-tauri/src/components/KioskHUD.jsx`

- [ ] **Step 1: Bump the face size.** In `src/voice-agent/desktop-tauri/src/components/KioskHUD.jsx`, change:
```javascript
const AURA_SIZE = 448
```
to:
```javascript
const AURA_SIZE = 576
```

- [ ] **Step 2: Build** — `cd src/voice-agent/desktop-tauri && npm run build 2>&1 | grep -iE "built in|error"` → `✓ built`, no errors.

- [ ] **Step 3: Full voice-agent regression** — `cd src/voice-agent && .venv/bin/python -m pytest tests/ -q` → all pass (prior total + the new expression/table tests, 0 failures).

- [ ] **Step 4: Restart the voice-client (engine lives there).** Check turn age first:
```bash
sqlite3 ~/.local/share/jarvis/turn_telemetry.db "SELECT CAST(strftime('%s','now') AS INTEGER) - CAST(strftime('%s', MAX(ts_utc)) AS INTEGER) FROM turns;"
```
If >60s (or after asking), restart only the voice-client:
```bash
systemctl --user restart jarvis-voice-client.service
```

- [ ] **Step 5: Live visual verify** (same method as the visemes). Serve the dev route + browser; speak an emotive line vs a flat line; sample `/face`; screenshot:
```bash
cd src/voice-agent/desktop-tauri && nohup npx vite preview --port 4180 --strictPort >/tmp/jx-vite.log 2>&1 &
sleep 2
DISPLAY=:0 nohup google-chrome --app='http://localhost:4180/?route=faceonly' --window-size=560,600 --user-data-dir=/tmp/jx-chrome --no-first-run >/tmp/jx-chrome.log 2>&1 &
sleep 5
for line in "That is wonderful, I am so happy!" "the file is in that folder"; do
  curl -s -X POST http://127.0.0.1:8767/speak -H 'Content-Type: application/json' -d "{\"text\":\"$line\"}" >/dev/null
  sleep 0.6; curl -s http://127.0.0.1:8767/face; echo " <- $line"; sleep 3
done
```
Expect the positive line's `/face` to carry brow/smile morphs (`target_0/3/4/37/38`) plus visemes; the flat line to carry visemes only. Screenshot the window mid-speech and confirm brows/cheeks move on the emotive line, neutral on the flat one. Clean up: `pkill -f 'jx-chrom[e]'`; kill the vite preview by PID.

- [ ] **Step 6: Deploy to the kiosk binary.**
```bash
cd src/voice-agent/desktop-tauri/src-tauri && cargo build --release
```
Then SIGKILL the running desktop binary (skips the tray Quit handler that stops voice) and relaunch: find PID via `pgrep -f 'jarvis-deskto[p]'`, `kill -9 <pid>` (+ any stragglers), then `setsid nohup bash src/cli/scripts/start-desktop.sh >/tmp/jx-redeploy.log 2>&1 </dev/null &`. Confirm desktop up, bridge `:8765` → 200, voice services `active`.

- [ ] **Step 7: User verifies in the real kiosk** — enter kiosk; confirm the face is bigger, brows/cheeks react to emotive replies, eyes are wide with occasional darts + brow flicks, mouth still lip-syncs.

- [ ] **Step 8: Commit**
```bash
git add src/voice-agent/desktop-tauri/src/components/KioskHUD.jsx
git commit -m "feat(expression): bump kiosk face size 448->576"
```

---

## Self-Review (completed during authoring)

**Spec coverage:** tables + presets (T1); ExpressionEngine VADER+punctuation (T2); voice-client merge (T3); kiosk wider apply + eyeWide baseline + idle micro-expressions (T4); size bump + verify + deploy (T5). All locked decisions covered. Disjoint-morph merge preserved (visemes never emit target_0-4/17-21/37-40 via resolve_pose; expression owns them).

**Placeholder scan:** none — every step has complete code + exact commands.

**Type consistency:** `expression_for_text`/`ExpressionEngine.frame(speaking)`/`set_pending_text` signatures consistent T2↔T3. `EXPRESSION_PRESETS` (T1) keyed by the four preset names used in `expression_for_text` (T2). `ARKIT_TO_TARGET` new keys (T1) match those referenced in presets (T1) and resolved in `_blend` (T2). Kiosk `EXPRESSION=[0,1,2,3,4,20,21,39,40]` (T4) ⊂ the morphs the engine can emit; eyeWide 17/18 handled separately; smile 37/38 already in `MOUTH`. Merge `{**_vw, **expr}` (T3) — disjoint, order-safe.

**OUT (untouched):** `jarvis_agent.py` + turn-router, the production-hardening WIP, `src/cli/`, the GLB/model (no Blender), `voice_client_http_api.py` (`/face` already serves `face_weights`).
