# JARVIS Face — Local Viseme Lip-Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the kiosk talking face from amplitude jaw-bob to real visemes by turning JARVIS's known TTS text + the audio RMS into Oculus-viseme → ARKit-morph weights, served on a new `GET /face` and applied to the FaceCap GLB's morphs in the kiosk — fully local on CPU/RAM, with the amplitude `/level` path preserved as a never-worse-than-today fallback.

**Architecture:** A new pure-Python `lipsync/` module (tables + a stateful `VisemeEngine`) lives in the voice-client. The transcription handler feeds it the agent's spoken text; the playback loop calls it per audio frame with `(now, speaking, rms)` and stores the resulting morph weights on shared state; a new `/face` endpoint publishes them; the kiosk polls `/face` and applies each weight to the named morph, with idle blinks + head-sway added kiosk-side. If no text is available the engine falls back to amplitude jaw, so behavior never regresses.

**Tech Stack:** Python 3.13 (voice-agent `.venv`), `cmudict` (offline pronunciation dictionary, no model/torch), aiohttp; three.js / @react-three/fiber in the Tauri kiosk. Tests: pytest (`src/voice-agent/.venv/bin/python -m pytest`).

**Spec:** [docs/superpowers/specs/2026-05-30-jarvis-face-viseme-lipsync-design.md](../specs/2026-05-30-jarvis-face-viseme-lipsync-design.md)

**Reference facts (verified against the live code):**
- `ClientState` is a `@dataclass` in `src/voice-agent/jarvis_voice_client.py` (~line 405); `output_level: float` already exists (line 417).
- The playback loop computes RMS at `state.output_level += (_lvl - state.output_level) * 0.5` (~line 580) and sets `state.speaking` just above (lines 571–575).
- The transcription handler `_drain_text_stream(reader, participant_identity)` (~line 763) currently **discards** text (`async for _ in reader: pass`); it's registered for `"lk.transcription"` (line 777). The local participant identity is `desktop-ulrich`; the agent's transcript arrives under a different identity.
- `voice_client_http_api.py`: `_CORS_HEADERS` (line 70), `level()` handler (line 178), routes registered in `build_app()` (line 124+).
- The FaceCap GLB uses the **canonical ARKit-52 order** — confirmed twice by existing code: `jawOpen = target_24` (FaceWebGL `Head`) and `eyeWideLeft/Right = target_17/target_18` (FaceWebGL sets them to 0.55). The full canonical index table is therefore trustworthy.

---

## Task 1: Viseme + morph tables (`lipsync/viseme_tables.py`)

**Files:**
- Create: `src/voice-agent/lipsync/__init__.py`
- Create: `src/voice-agent/lipsync/viseme_tables.py`
- Test: `src/voice-agent/tests/test_viseme_tables.py`

- [ ] **Step 1: Create the package init**

`src/voice-agent/lipsync/__init__.py`:
```python
"""Local viseme lip-sync for the kiosk face.

Turns JARVIS's known TTS text + audio RMS into ARKit-morph weights that
drive the FaceCap GLB. Pure CPU/RAM — no GPU, no neural net. See
docs/superpowers/specs/2026-05-30-jarvis-face-viseme-lipsync-design.md.
"""
from .viseme_engine import VisemeEngine

__all__ = ["VisemeEngine"]
```

- [ ] **Step 2: Write the failing test**

`src/voice-agent/tests/test_viseme_tables.py`:
```python
from lipsync import viseme_tables as vt


def test_jaw_open_maps_to_target_24():
    # Confirmed twice in the live kiosk code (FaceWebGL).
    assert vt.ARKIT_TO_TARGET["jawOpen"] == "target_24"
    assert vt.ARKIT_TO_TARGET["eyeWideLeft"] == "target_17"
    assert vt.ARKIT_TO_TARGET["eyeBlinkLeft"] == "target_13"


def test_every_arpabet_phoneme_maps_to_a_viseme():
    # 39 ARPAbet phonemes (CMU set, stress digits stripped).
    arpabet = {
        "AA","AE","AH","AO","AW","AY","B","CH","D","DH","EH","ER","EY",
        "F","G","HH","IH","IY","JH","K","L","M","N","NG","OW","OY","P",
        "R","S","SH","T","TH","UH","UW","V","W","Y","Z","ZH",
    }
    for p in arpabet:
        assert p in vt.ARPABET_TO_VISEME, f"{p} unmapped"
        assert vt.ARPABET_TO_VISEME[p] in vt.VISEMES


def test_every_viseme_pose_uses_known_arkit_names():
    for viseme, pose in vt.VISEME_TO_ARKIT.items():
        assert viseme in vt.VISEMES, f"pose for unknown viseme {viseme}"
        for name, weight in pose.items():
            assert name in vt.ARKIT_TO_TARGET, f"{name} not in ARKIT_TO_TARGET"
            assert 0.0 <= weight <= 1.0


def test_resolve_pose_returns_target_indexed_weights():
    weights = vt.resolve_pose("aa", openness=1.0)
    # 'aa' opens the jaw; key must be the target_N name, not 'jawOpen'.
    assert weights["target_24"] > 0.5
    assert all(k.startswith("target_") for k in weights)


def test_resolve_pose_scales_by_openness():
    full = vt.resolve_pose("aa", openness=1.0)["target_24"]
    half = vt.resolve_pose("aa", openness=0.5)["target_24"]
    assert abs(half - full * 0.5) < 1e-6
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_viseme_tables.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'lipsync.viseme_tables'` (or AttributeError).

- [ ] **Step 4: Write the tables**

`src/voice-agent/lipsync/viseme_tables.py`:
```python
"""Static lookup tables for viseme lip-sync.

Three maps, all hand-authored and pure data:
  ARPABET_TO_VISEME  — CMU ARPAbet phoneme -> Oculus 15-viseme code
  VISEME_TO_ARKIT    — Oculus viseme -> {ARKit-morph-name: weight 0..1}
  ARKIT_TO_TARGET    — ARKit-morph-name -> FaceCap GLB morph key 'target_N'

The Oculus viseme vocabulary and the phoneme->viseme grouping follow the
met4citizen/TalkingHead (MIT) and Oculus LipSync conventions. The
ARKit-52 canonical ordering matches the FaceCap GLB (jawOpen=24,
eyeWideL/R=17/18 are both confirmed in the kiosk code).
"""
from __future__ import annotations

# Oculus 15-viseme set.
VISEMES = (
    "sil", "PP", "FF", "TH", "DD", "kk", "CH", "SS",
    "nn", "RR", "aa", "E", "ih", "oh", "ou",
)

# CMU ARPAbet phoneme (stress digits already stripped by the caller) -> viseme.
ARPABET_TO_VISEME = {
    # vowels
    "AA": "aa", "AE": "aa", "AH": "aa", "AY": "aa", "AW": "aa",
    "AO": "oh", "OW": "oh", "OY": "oh",
    "EH": "E",  "EY": "E",
    "ER": "RR",
    "IH": "ih", "IY": "ih",
    "UH": "ou", "UW": "ou",
    # consonants
    "B": "PP", "P": "PP", "M": "PP",
    "F": "FF", "V": "FF",
    "TH": "TH", "DH": "TH",
    "D": "DD", "T": "DD",
    "K": "kk", "G": "kk", "NG": "kk",
    "CH": "CH", "JH": "CH", "SH": "CH", "ZH": "CH",
    "S": "SS", "Z": "SS",
    "N": "nn", "L": "nn",
    "R": "RR",
    "HH": "aa",
    "W": "ou",
    "Y": "ih",
}

# ARKit-52 canonical name -> FaceCap GLB morph key. Only the channels the
# viseme + idle-life layers actually use are listed (the GLB has all 52).
ARKIT_TO_TARGET = {
    "eyeBlinkLeft":      "target_13",
    "eyeBlinkRight":     "target_14",
    "eyeWideLeft":       "target_17",
    "eyeWideRight":      "target_18",
    "jawOpen":           "target_24",
    "mouthFunnel":       "target_28",
    "mouthPucker":       "target_29",
    "mouthClose":        "target_36",
    "mouthSmileLeft":    "target_37",
    "mouthSmileRight":   "target_38",
    "mouthUpperUpLeft":  "target_43",
    "mouthUpperUpRight": "target_44",
    "mouthLowerDownLeft":  "target_45",
    "mouthLowerDownRight": "target_46",
    "mouthPressLeft":    "target_47",
    "mouthPressRight":   "target_48",
    "mouthStretchLeft":  "target_49",
    "mouthStretchRight": "target_50",
    "tongueOut":         "target_51",
}

# Each viseme -> the mouth pose it holds at full openness (weights 0..1).
# 'sil' is the closed rest pose. Openness (the RMS gate) scales the whole
# pose at resolve time.
VISEME_TO_ARKIT = {
    "sil": {},
    "PP":  {"mouthClose": 0.9, "mouthPressLeft": 0.4, "mouthPressRight": 0.4},
    "FF":  {"jawOpen": 0.12, "mouthFunnel": 0.2, "mouthLowerDownLeft": 0.2, "mouthLowerDownRight": 0.2},
    "TH":  {"jawOpen": 0.2, "tongueOut": 0.3},
    "DD":  {"jawOpen": 0.2, "mouthStretchLeft": 0.1, "mouthStretchRight": 0.1},
    "kk":  {"jawOpen": 0.25},
    "CH":  {"jawOpen": 0.2, "mouthFunnel": 0.4, "mouthPucker": 0.3},
    "SS":  {"jawOpen": 0.1, "mouthStretchLeft": 0.3, "mouthStretchRight": 0.3},
    "nn":  {"jawOpen": 0.15, "mouthUpperUpLeft": 0.1, "mouthUpperUpRight": 0.1},
    "RR":  {"jawOpen": 0.2, "mouthPucker": 0.3},
    "aa":  {"jawOpen": 0.7, "mouthLowerDownLeft": 0.2, "mouthLowerDownRight": 0.2},
    "E":   {"jawOpen": 0.4, "mouthStretchLeft": 0.3, "mouthStretchRight": 0.3},
    "ih":  {"jawOpen": 0.25, "mouthStretchLeft": 0.2, "mouthStretchRight": 0.2},
    "oh":  {"jawOpen": 0.45, "mouthFunnel": 0.5, "mouthPucker": 0.3},
    "ou":  {"jawOpen": 0.3, "mouthFunnel": 0.4, "mouthPucker": 0.7},
}


def resolve_pose(viseme: str, openness: float) -> dict[str, float]:
    """Return {target_N: weight} for `viseme`, every weight scaled by
    `openness` (0..1, the RMS gate). Unknown viseme -> closed mouth ({})."""
    o = max(0.0, min(1.0, openness))
    pose = VISEME_TO_ARKIT.get(viseme, {})
    out: dict[str, float] = {}
    for name, w in pose.items():
        out[ARKIT_TO_TARGET[name]] = round(w * o, 4)
    return out
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_viseme_tables.py -q`
Expected: PASS (5 passed).

- [ ] **Step 6: Commit**

```bash
git add src/voice-agent/lipsync/__init__.py src/voice-agent/lipsync/viseme_tables.py src/voice-agent/tests/test_viseme_tables.py
git commit -m "feat(lipsync): viseme + ARKit-morph tables (Oculus 15-viseme -> FaceCap target_N)"
```

---

## Task 2: Phonemizer (`lipsync/phonemize.py`)

**Files:**
- Create: `src/voice-agent/lipsync/phonemize.py`
- Modify: `src/voice-agent/requirements.txt` (add `cmudict`)
- Test: `src/voice-agent/tests/test_phonemize.py`

- [ ] **Step 1: Add the dependency and install it**

Append to `src/voice-agent/requirements.txt`:
```
cmudict  # offline CMU pronunciation dictionary for viseme lip-sync (no model/torch)
```
Run: `cd src/voice-agent && .venv/bin/pip install cmudict`
Expected: `Successfully installed cmudict-...`

- [ ] **Step 2: Write the failing test**

`src/voice-agent/tests/test_phonemize.py`:
```python
from lipsync.phonemize import text_to_visemes


def test_known_word_produces_visemes():
    vis = text_to_visemes("hello")
    assert isinstance(vis, list)
    assert len(vis) >= 3
    assert all(isinstance(v, str) for v in vis)


def test_silence_between_words():
    vis = text_to_visemes("hi there")
    assert "sil" in vis  # word boundary inserts a brief closure


def test_empty_text_is_empty():
    assert text_to_visemes("") == []
    assert text_to_visemes("   ") == []


def test_out_of_vocabulary_word_still_returns_visemes():
    # gibberish isn't in CMU dict -> letter fallback, must not crash/empty.
    vis = text_to_visemes("zxqwbf")
    assert len(vis) >= 1


def test_punctuation_and_case_ignored():
    a = text_to_visemes("Hello, World!")
    b = text_to_visemes("hello world")
    assert a == b
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_phonemize.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'lipsync.phonemize'`.

- [ ] **Step 4: Implement the phonemizer**

`src/voice-agent/lipsync/phonemize.py`:
```python
"""Text -> Oculus-viseme sequence, fully offline.

Known words come from the CMU pronouncing dictionary (ARPAbet); unknown
words fall back to a crude letter->phoneme rule so the mouth still moves.
A 'sil' is inserted at word boundaries for a brief closure. Stress digits
are stripped from ARPAbet symbols before the viseme lookup.
"""
from __future__ import annotations

import re

import cmudict

from .viseme_tables import ARPABET_TO_VISEME

_CMU = cmudict.dict()  # {word: [[phoneme, ...], ...]}, loaded once
_WORD_RE = re.compile(r"[a-z']+")
_STRESS_RE = re.compile(r"\d")

# Crude single-letter ARPAbet fallback for out-of-vocabulary words.
_LETTER_PHONEME = {
    "a": "AE", "b": "B", "c": "K", "d": "D", "e": "EH", "f": "F",
    "g": "G", "h": "HH", "i": "IH", "j": "JH", "k": "K", "l": "L",
    "m": "M", "n": "N", "o": "OW", "p": "P", "q": "K", "r": "R",
    "s": "S", "t": "T", "u": "AH", "v": "V", "w": "W", "x": "K",
    "y": "Y", "z": "Z",
}


def _phonemes_for(word: str) -> list[str]:
    entry = _CMU.get(word)
    if entry:
        return [_STRESS_RE.sub("", p) for p in entry[0]]
    return [_LETTER_PHONEME[c] for c in word if c in _LETTER_PHONEME]


def text_to_visemes(text: str) -> list[str]:
    """Return a flat list of Oculus viseme codes for `text`, with 'sil'
    at word boundaries. Empty/whitespace -> []."""
    words = _WORD_RE.findall(text.lower())
    out: list[str] = []
    for i, word in enumerate(words):
        if i > 0:
            out.append("sil")
        for ph in _phonemes_for(word):
            out.append(ARPABET_TO_VISEME.get(ph, "sil"))
    return out
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_phonemize.py -q`
Expected: PASS (5 passed).

- [ ] **Step 6: Commit**

```bash
git add src/voice-agent/lipsync/phonemize.py src/voice-agent/tests/test_phonemize.py src/voice-agent/requirements.txt
git commit -m "feat(lipsync): offline text->viseme phonemizer (cmudict + letter fallback)"
```

---

## Task 3: The stateful engine (`lipsync/viseme_engine.py`)

**Files:**
- Create: `src/voice-agent/lipsync/viseme_engine.py`
- Test: `src/voice-agent/tests/test_viseme_engine.py`

**Design of the engine:** one instance, shared between the transcription handler and the playback loop.
- `set_pending_text(text)` — called when the agent's transcript arrives; stashes the next utterance's text.
- `frame(now, speaking, rms)` — called per playback frame; returns `{target_N: weight}`. It detects the `speaking` rising edge (starts the sequence from the pending text at `t0=now`), advances a cursor by elapsed time against per-viseme nominal durations, gates openness by `rms`, and on the falling edge resets. If `speaking` but there's no text sequence, it falls back to amplitude jaw (`{target_24: clamp(rms*GAIN)}`) so behavior never regresses.

- [ ] **Step 1: Write the failing test**

`src/voice-agent/tests/test_viseme_engine.py`:
```python
from lipsync.viseme_engine import VisemeEngine


def test_silent_returns_empty():
    eng = VisemeEngine()
    assert eng.frame(now=0.0, speaking=False, rms=0.0) == {}


def test_speaking_without_text_falls_back_to_amplitude_jaw():
    eng = VisemeEngine()
    out = eng.frame(now=0.0, speaking=True, rms=0.1)
    # amplitude fallback drives only the jaw, scaled by rms*gain.
    assert set(out) == {"target_24"}
    assert out["target_24"] > 0.0


def test_speaking_with_text_drives_mouth_visemes():
    eng = VisemeEngine()
    eng.set_pending_text("hello world")
    eng.frame(now=0.0, speaking=True, rms=0.2)          # rising edge -> t0
    out = eng.frame(now=0.15, speaking=True, rms=0.2)   # 150 ms in
    assert "target_24" in out                            # jaw active
    assert all(k.startswith("target_") for k in out)
    assert all(0.0 <= v <= 1.0 for v in out.values())


def test_openness_tracks_rms():
    eng = VisemeEngine()
    eng.set_pending_text("aaaa")
    eng.frame(now=0.0, speaking=True, rms=0.4)
    loud = eng.frame(now=0.05, speaking=True, rms=0.4)["target_24"]
    eng.reset()
    eng.set_pending_text("aaaa")
    eng.frame(now=0.0, speaking=True, rms=0.05)
    quiet = eng.frame(now=0.05, speaking=True, rms=0.05)["target_24"]
    assert loud > quiet


def test_falling_edge_resets():
    eng = VisemeEngine()
    eng.set_pending_text("hello")
    eng.frame(now=0.0, speaking=True, rms=0.2)
    eng.frame(now=0.1, speaking=True, rms=0.2)
    assert eng.frame(now=0.2, speaking=False, rms=0.0) == {}
    # a new utterance can start cleanly after the reset
    eng.set_pending_text("hi")
    out = eng.frame(now=1.0, speaking=True, rms=0.2)
    assert out  # non-empty (fallback or visemes), no stale state crash
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_viseme_engine.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'lipsync.viseme_engine'`.

- [ ] **Step 3: Implement the engine**

`src/voice-agent/lipsync/viseme_engine.py`:
```python
"""Stateful viseme engine — turns (text, audio RMS) into per-frame morph
weights. One instance shared by the transcription handler and the
playback loop. Pure CPU. See the design spec.
"""
from __future__ import annotations

from .phonemize import text_to_visemes
from .viseme_tables import resolve_pose

# Each viseme holds for this long before the cursor advances. Conversational
# speech is ~12 phonemes/sec, so ~80 ms/viseme tracks naturally; 'sil'
# closures are shorter.
_VISEME_DUR_S = 0.08
_SIL_DUR_S = 0.04
# RMS at/above this reads as full openness (matches the 0..~0.2 range the
# playback loop produces for /level).
_RMS_FULL = 0.18
# Amplitude-fallback jaw gain (mirrors the kiosk's JAW_GAIN=6.0 today).
_FALLBACK_JAW_GAIN = 6.0


class VisemeEngine:
    def __init__(self) -> None:
        self._pending_text: str = ""
        self._seq: list[str] = []          # active viseme sequence
        self._durs: list[float] = []       # cumulative end-time of each viseme
        self._t0: float | None = None      # utterance start (rising edge)
        self._was_speaking: bool = False

    def set_pending_text(self, text: str) -> None:
        """Stash the text of the utterance about to be voiced."""
        self._pending_text = (text or "").strip()

    def reset(self) -> None:
        self._seq = []
        self._durs = []
        self._t0 = None
        self._was_speaking = False

    def _start(self, now: float) -> None:
        vis = text_to_visemes(self._pending_text)
        self._seq = vis
        # cumulative end-times so we can binary-walk the cursor by elapsed time
        self._durs = []
        t = 0.0
        for v in vis:
            t += _SIL_DUR_S if v == "sil" else _VISEME_DUR_S
            self._durs.append(t)
        self._t0 = now

    def frame(self, now: float, speaking: bool, rms: float) -> dict[str, float]:
        """Return {target_N: weight} for the current frame."""
        # falling edge -> reset, mouth closes (kiosk eases to 0)
        if not speaking:
            if self._was_speaking:
                self.reset()
            self._was_speaking = False
            return {}
        # rising edge -> build the sequence from the pending text
        if not self._was_speaking:
            self._start(now)
            self._was_speaking = True

        openness = max(0.0, min(1.0, rms / _RMS_FULL))

        # no usable sequence -> amplitude jaw fallback (never worse than today)
        if not self._seq:
            jaw = max(0.0, min(1.0, rms * _FALLBACK_JAW_GAIN))
            return {"target_24": round(jaw, 4)}

        elapsed = now - (self._t0 or now)
        # find the current viseme by cumulative end-time; hold the last one
        # if the audio outruns the sequence.
        idx = len(self._seq) - 1
        for i, end_t in enumerate(self._durs):
            if elapsed < end_t:
                idx = i
                break
        return resolve_pose(self._seq[idx], openness)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_viseme_engine.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/lipsync/viseme_engine.py src/voice-agent/tests/test_viseme_engine.py
git commit -m "feat(lipsync): stateful VisemeEngine (RMS-gated text timing + amplitude fallback)"
```

---

## Task 4: Wire the engine into the voice-client

**Files:**
- Modify: `src/voice-agent/jarvis_voice_client.py` (ClientState field; engine singleton; transcription capture; playback-loop call)

- [ ] **Step 1: Add `face_weights` to `ClientState`**

In `src/voice-agent/jarvis_voice_client.py`, right after the `output_level` field (~line 417), add:
```python
    # Current frame's ARKit-morph weights {target_N: 0..1} for the kiosk
    # face's visemes. Updated by the playback loop via the VisemeEngine;
    # published on GET /face. Empty dict = mouth at rest.
    face_weights:  dict = field(default_factory=dict)
```
Ensure `field` is imported — at the top of the file change the dataclasses import to include it:
```python
from dataclasses import dataclass, field
```
(If `field` is already imported, leave the import as-is.)

- [ ] **Step 2: Create the engine singleton**

Near the other module-level singletons in `jarvis_voice_client.py` (e.g. by `_reverse_estimator`), add:
```python
from lipsync import VisemeEngine
_viseme_engine = VisemeEngine()
```

- [ ] **Step 3: Capture the agent's transcript text**

Replace the body of `_drain_text_stream` (~line 763) so it feeds the engine. The local user's identity is `desktop-ulrich`; only the *remote* (agent) transcript should drive the face:
```python
    async def _drain_text_stream(reader, participant_identity: str) -> None:
        try:
            buf = []
            async for chunk in reader:
                buf.append(chunk)
            text = "".join(buf).strip()
            # Only the agent's TTS transcript drives the face, not our own
            # STT transcript echoed back under the local identity.
            if text and participant_identity != "desktop-ulrich":
                _viseme_engine.set_pending_text(text)
        except Exception as e:
            log.debug(f"[stream-drain] text stream from {participant_identity} ended: {e}")
```
(If the local identity is sourced from a constant/variable rather than the literal `"desktop-ulrich"`, use that; grep `desktop-ulrich` to confirm the canonical source before hardcoding.)

- [ ] **Step 4: Drive `face_weights` from the playback loop**

In the playback loop, immediately after the `state.output_level += (_lvl - state.output_level) * 0.5` line (~line 580), add:
```python
            # Viseme lip-sync: resolve this frame's ARKit-morph weights from
            # the agent's known text + the smoothed RMS. Never raises into
            # the audio path — on any error the face just falls back to rest.
            try:
                state.face_weights = _viseme_engine.frame(
                    now=time.monotonic(),
                    speaking=state.speaking,
                    rms=state.output_level,
                )
            except Exception as e:
                log.debug(f"[lipsync] frame failed: {e}")
                state.face_weights = {}
```

- [ ] **Step 5: Verify nothing broke (import + full suite)**

Run: `cd src/voice-agent && .venv/bin/python -c "import jarvis_voice_client" && .venv/bin/python -m pytest tests/ -q`
Expected: import succeeds; full suite still passes (2827+ passed, same as before plus the new lipsync tests).

- [ ] **Step 6: Commit**

```bash
git add src/voice-agent/jarvis_voice_client.py
git commit -m "feat(lipsync): wire VisemeEngine into the voice-client (transcript -> engine -> state.face_weights)"
```

---

## Task 5: Serve the weights on `GET /face`

**Files:**
- Modify: `src/voice-agent/voice_client_http_api.py` (route + handler)
- Test: `src/voice-agent/tests/test_face_endpoint.py`

- [ ] **Step 1: Write the failing test**

`src/voice-agent/tests/test_face_endpoint.py`:
```python
import asyncio
import logging
from dataclasses import dataclass, field

from voice_client_http_api import VoiceClientHttpApi


@dataclass
class _FakeState:
    output_level: float = 0.0
    face_weights: dict = field(default_factory=dict)


def _make_api(state):
    return VoiceClientHttpApi(
        state=state,
        get_mic_pub=lambda: None,
        get_room=lambda: None,
        restart_agent_unit=lambda: asyncio.sleep(0),
        log=logging.getLogger("test"),
    )


def test_face_route_is_registered():
    api = _make_api(_FakeState())
    app = api.build_app()
    paths = {r.resource.canonical for r in app.router.routes()}
    assert "/face" in paths


def test_face_returns_weights_and_level():
    state = _FakeState(output_level=0.17, face_weights={"target_24": 0.6})
    api = _make_api(state)

    async def go():
        from aiohttp.test_utils import make_mocked_request
        resp = await api.face(make_mocked_request("GET", "/face"))
        import json
        return json.loads(resp.body.decode())

    body = asyncio.run(go())
    assert body["weights"] == {"target_24": 0.6}
    assert body["level"] == 0.17
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_face_endpoint.py -q`
Expected: FAIL (`/face` not registered; `api.face` AttributeError).

- [ ] **Step 3: Register the route and add the handler**

In `build_app()` (`voice_client_http_api.py`, right after the `/level` line ~125):
```python
        app.router.add_get("/face",    self.face)    # per-frame viseme morph weights
```
Add the handler right after `level()` (~line 184):
```python
    async def face(self, _: web.Request) -> web.Response:
        """GET /face — the current frame's ARKit-morph weights
        {target_N: 0..1} plus the raw level, polled ~30-60fps by the
        kiosk to drive the WebGL face's visemes. Empty weights = at rest;
        the kiosk then falls back to amplitude jaw from `level`."""
        return web.json_response(
            {"weights": getattr(self.state, "face_weights", {}) or {},
             "level": round(self.state.output_level, 4)},
            headers=_CORS_HEADERS,
        )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_face_endpoint.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/voice_client_http_api.py src/voice-agent/tests/test_face_endpoint.py
git commit -m "feat(lipsync): GET /face endpoint serving per-frame viseme morph weights"
```

---

## Task 6: Apply multi-morph weights in the kiosk

**Files:**
- Modify: `src/desktop-tauri/src/components/FaceWebGL.jsx`
- Modify: `src/desktop-tauri/src/components/KioskHUD.jsx`

- [ ] **Step 1: Poll `/face` in KioskHUD and pass a weights getter**

In `src/desktop-tauri/src/components/KioskHUD.jsx`: add a `FACE_URL`, change the jaw ref to a weights ref, and swap the poll. Replace the `LEVEL_URL` const (line 21) region with:
```javascript
const STATUS_URL = 'http://127.0.0.1:8767/status'
const FACE_URL   = 'http://127.0.0.1:8767/face'
const STATUS_POLL_MS = 500
const FACE_POLL_MS    = 33        // ~30 fps; useFrame smooths between samples
const JAW_GAIN = 6.0              // fallback only: /face.weights empty -> jaw from level
const AURA_SIZE = 448
```
Replace the `jawRef` declaration (line 40) with a weights ref:
```javascript
  // {target_N: 0..1} for the current frame, updated off-React by the
  // /face poll (no per-frame re-render, per the reactor-removed rule).
  const weightsRef = useRef({})
```
Replace the entire `/level` poll effect (lines 90–106) with a `/face` poll:
```javascript
  // Poll /face fast and drive the morphs (off-React — writes a ref).
  useEffect(() => {
    let cancelled = false
    const id = setInterval(async () => {
      try {
        const r = await fetch(FACE_URL)
        const d = await r.json()
        if (cancelled) return
        const w = d.weights && Object.keys(d.weights).length
          ? d.weights
          : { target_24: Math.max(0, Math.min(1, (d.level || 0) * JAW_GAIN)) }
        weightsRef.current = w
      } catch {
        if (!cancelled) weightsRef.current = {}
      }
    }, FACE_POLL_MS)
    return () => { cancelled = true; clearInterval(id) }
  }, [])
```
Change the `<FaceWebGL .../>` usage (line 142) to pass the weights getter:
```javascript
        <FaceWebGL size={AURA_SIZE} getWeights={() => weightsRef.current} />
```

- [ ] **Step 2: Apply the weights to named morphs in FaceWebGL**

In `src/desktop-tauri/src/components/FaceWebGL.jsx`, replace the `Head` component's jaw bookkeeping with a generic name→index map and multi-morph easing. Replace the `Head` function (lines 17–64) with:
```javascript
function Head({ getWeights }) {
  const { scene } = useGLTF(MODEL_URL)
  const headRef = useRef(null)
  const idxByTargetRef = useRef({})   // 'target_24' -> influence index

  // Find the head mesh (carries the jaw morph), build the target->index
  // map once, tint the skin, set a static eyeWide pose.
  useMemo(() => {
    scene.traverse((o) => {
      if (!o.isMesh) return
      o.frustumCulled = false
      if (o.morphTargetDictionary && 'target_24' in o.morphTargetDictionary) {
        headRef.current = o
        idxByTargetRef.current = o.morphTargetDictionary
        const inf = o.morphTargetInfluences
        const eL = o.morphTargetDictionary['target_17']
        const eR = o.morphTargetDictionary['target_18']
        if (inf && eL != null) inf[eL] = 0.55
        if (inf && eR != null) inf[eR] = 0.55
        if (o.material) {
          o.material.color = new THREE.Color(SKIN_TINT)
          o.material.roughness = 0.72
          o.material.metalness = 0.0
        }
      }
    })
  }, [scene])

  useFrame(() => {
    const h = headRef.current
    if (!h || !h.morphTargetInfluences) return
    const dict = idxByTargetRef.current
    const targets = (getWeights && getWeights()) || {}
    // Mouth/viseme morphs we drive every frame (eyes/brows excluded so the
    // static eyeWide + idle blinks aren't fought).
    const MOUTH = [24, 28, 29, 36, 37, 38, 43, 44, 45, 46, 47, 48, 49, 50, 51]
    for (const n of MOUTH) {
      const key = 'target_' + n
      const i = dict[key]
      if (i == null) continue
      const target = Math.max(0, Math.min(1, targets[key] || 0))
      const cur = h.morphTargetInfluences[i] || 0
      const k = target > cur ? 0.4 : 0.25     // open fast, close slower
      h.morphTargetInfluences[i] = cur + (target - cur) * k
    }
  })

  return (
    <Center>
      <group rotation={HEAD_ROT}>
        <primitive object={scene} />
      </group>
    </Center>
  )
}
```
Update the `FaceWebGL` export's prop (line 66) and the `<Head/>` usage (line 78):
```javascript
export function FaceWebGL({ size, getWeights }) {
```
```javascript
        <Head getWeights={getWeights} />
```

- [ ] **Step 3: Build to verify it compiles**

Run: `cd src/desktop-tauri && npm run build 2>&1 | grep -iE "built in|error"`
Expected: `✓ built in ...` with no errors.

- [ ] **Step 4: Commit**

```bash
git add src/desktop-tauri/src/components/FaceWebGL.jsx src/desktop-tauri/src/components/KioskHUD.jsx
git commit -m "feat(lipsync): kiosk applies per-frame viseme morph weights from /face (jaw fallback kept)"
```

---

## Task 7: Idle life — blinks + head sway

**Files:**
- Modify: `src/desktop-tauri/src/components/FaceWebGL.jsx`

- [ ] **Step 1: Add blink + sway state to `Head`**

In `FaceWebGL.jsx` `Head`, add refs after `idxByTargetRef`:
```javascript
  const groupRef = useRef(null)        // head group, for sway
  const blinkRef = useRef({ next: 2.0, t: -1 })  // next blink time, active start
  const clockRef = useRef(0)
```

- [ ] **Step 2: Attach a ref to the head group**

Change the returned group to take the ref:
```javascript
  return (
    <Center>
      <group ref={groupRef} rotation={HEAD_ROT}>
        <primitive object={scene} />
      </group>
    </Center>
  )
```

- [ ] **Step 3: Drive blink + sway in `useFrame`**

Extend the `useFrame` callback in `Head` (append inside it, after the mouth loop), passing `delta`:
```javascript
  useFrame((_, delta) => {
    const h = headRef.current
    if (!h || !h.morphTargetInfluences) return
    const dict = idxByTargetRef.current
    const inf = h.morphTargetInfluences
    const targets = (getWeights && getWeights()) || {}
    const MOUTH = [24, 28, 29, 36, 37, 38, 43, 44, 45, 46, 47, 48, 49, 50, 51]
    for (const n of MOUTH) {
      const key = 'target_' + n
      const i = dict[key]
      if (i == null) continue
      const target = Math.max(0, Math.min(1, targets[key] || 0))
      const cur = inf[i] || 0
      const k = target > cur ? 0.4 : 0.25
      inf[i] = cur + (target - cur) * k
    }

    // ── idle life ──────────────────────────────────────────────
    const now = (clockRef.current += delta)
    // Blink: schedule every 3–6 s; a blink is a ~120 ms close→open.
    const bl = dict['target_13'], br = dict['target_14']
    const blink = blinkRef.current
    if (blink.t < 0 && now >= blink.next) { blink.t = now }
    let blinkVal = 0
    if (blink.t >= 0) {
      const p = (now - blink.t) / 0.12           // 0..1 over 120 ms
      if (p >= 1) { blink.t = -1; blink.next = now + 3 + Math.random() * 3 }
      else { blinkVal = Math.sin(p * Math.PI) }  // 0→1→0
    }
    if (bl != null) inf[bl] = blinkVal
    if (br != null) inf[br] = blinkVal

    // Head sway: gentle, damped while the mouth is active so it doesn't
    // fight visemes.
    const g = groupRef.current
    if (g) {
      const jaw = inf[dict['target_24']] || 0
      const amp = (1 - Math.min(1, jaw * 2)) * 0.026   // ~±1.5° at rest
      g.rotation.z = HEAD_ROT[2] + Math.sin(now * 0.6) * amp
      g.rotation.y = Math.sin(now * 0.43) * amp * 0.6
    }
  })
```
(Replace the existing `useFrame(() => { ... })` from Task 6 Step 2 with this single combined `useFrame((_, delta) => { ... })` — there must be only one.)

- [ ] **Step 4: Build to verify it compiles**

Run: `cd src/desktop-tauri && npm run build 2>&1 | grep -iE "built in|error"`
Expected: `✓ built in ...` no errors.

- [ ] **Step 5: Commit**

```bash
git add src/desktop-tauri/src/components/FaceWebGL.jsx
git commit -m "feat(lipsync): idle life — randomized blinks + subtle head sway"
```

---

## Task 8: Dev preview on `/face`, end-to-end verify, deploy

**Files:**
- Modify: `src/desktop-tauri/src/App.jsx` (`FaceOnlyDev` polls `/face`, passes `getWeights`)

- [ ] **Step 1: Point the dev preview at `/face`**

In `src/desktop-tauri/src/App.jsx`, update `FaceOnlyDev` to poll `/face` and pass weights (mirror the kiosk). Replace the poll effect + render:
```javascript
  const weightsRef = useRef({})
  useEffect(() => {
    if (forced != null) { weightsRef.current = { target_24: forced }; return }
    const id = setInterval(async () => {
      try {
        const r = await fetch('http://127.0.0.1:8767/face')
        const d = await r.json()
        weightsRef.current = (d.weights && Object.keys(d.weights).length)
          ? d.weights
          : { target_24: Math.max(0, Math.min(1, (d.level || 0) * 6.0)) }
      } catch { weightsRef.current = {} }
    }, 33)
    return () => clearInterval(id)
  }, [forced])
  return (
    <div style={{ position: 'fixed', inset: 0, background: '#000',
                  display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <FaceWebGL size={sz} getWeights={() => weightsRef.current} />
    </div>
  )
```

- [ ] **Step 2: Build**

Run: `cd src/desktop-tauri && npm run build 2>&1 | grep -iE "built in|error"`
Expected: `✓ built in ...` no errors.

- [ ] **Step 3: Full voice-agent regression**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/ -q`
Expected: all pass (prior 2827 + the new lipsync/face tests; 0 failures).

- [ ] **Step 4: Restart the voice-client so the engine wiring goes live**

Check the latest turn age first (don't bounce voice within 60s of a live turn):
```bash
sqlite3 ~/.local/share/jarvis/turn_telemetry.db "SELECT CAST(strftime('%s','now') AS INTEGER) - CAST(strftime('%s', MAX(ts_utc)) AS INTEGER) FROM turns;"
```
If >60s (or after asking the user), restart **only the voice-client** (the engine lives there; the voice-agent brain is untouched):
```bash
systemctl --user restart jarvis-voice-client.service
```

- [ ] **Step 5: Verify the chain with a browser, like /level was verified**

Stand up a preview server + a throwaway browser window on the dev route, fire a `/speak`, sample `/face` + screenshot mid-utterance:
```bash
cd src/desktop-tauri && nohup npx vite preview --port 4178 --strictPort >/tmp/jv.log 2>&1 &
sleep 2
DISPLAY=:0 nohup google-chrome --app='http://localhost:4178/?route=faceonly' \
  --window-size=480,520 --user-data-dir=/tmp/jv-chrome --no-first-run >/tmp/jvc.log 2>&1 &
sleep 5
curl -s -X POST http://127.0.0.1:8767/speak -H 'Content-Type: application/json' \
  -d '{"text":"Viseme lip sync online. Watch my mouth form the words."}' >/dev/null &
WID=$(DISPLAY=:0 xdotool search --class chrome | tail -1)
for i in $(seq -w 1 20); do
  W=$(curl -s http://127.0.0.1:8767/face)
  DISPLAY=:0 import -window $WID /tmp/jv-$i.png 2>/dev/null
  echo "$i $W"; sleep 0.25
done
```
Read several `/tmp/jv-*.png` frames (silence vs mid-speech): the mouth must form varied shapes (not just open/close), closed on silence. Clean up: `pkill -f 'jv-chrom[e]'; kill the vite preview by PID`.

- [ ] **Step 6: Deploy to the kiosk binary**

```bash
cd src/desktop-tauri/src-tauri && cargo build --release
```
Then SIGKILL the running desktop binary (uncatchable → skips the tray Quit handler that stops voice) and re-launch via the launcher (its voice block is `is-active`-guarded). Find the PID with `pgrep -af 'jarvis-deskto[p]'`, `kill -9 <pid>`, then `setsid nohup bash src/cli/scripts/start-desktop.sh >/tmp/relaunch.log 2>&1 </dev/null &`. Confirm `nvidia-smi`-style: desktop back up, bridge `:8765` 200, voice services still `active`.

- [ ] **Step 7: User verifies in the real kiosk**

Ask the user to enter kiosk from the tray and confirm the mouth forms word shapes (visemes), blinks periodically, and sways subtly while idle.

- [ ] **Step 8: Commit**

```bash
git add src/desktop-tauri/src/App.jsx
git commit -m "feat(lipsync): dev face preview polls /face; end-to-end viseme verification"
```

---

## Self-Review (completed during authoring)

**Spec coverage:** viseme engine (T2–T3), tables incl. ARKit-name→target map (T1), `/face` transport (T5), voice-client wiring exploiting `lk.transcription` text + RMS (T4), kiosk multi-morph apply with `/level` fallback (T6), idle blinks + head sway (T7), dev-route + browser verification + deploy (T8). Oculus-viseme vocabulary (T1). All locked decisions covered.

**Placeholder scan:** no TBD/TODO; every code step is complete and runnable; error handling is concrete (engine try/except in the audio path, empty-weights fallback, `/face` empty → kiosk amplitude jaw).

**Type consistency:** `face_weights: dict` (T4) ↔ `/face` `{weights, level}` (T5) ↔ kiosk `weightsRef`/`getWeights` (T6) ↔ engine returns `{target_N: float}` (T3) ↔ `resolve_pose` returns `target_N` keys (T1). `set_pending_text`/`frame(now, speaking, rms)`/`reset` signatures consistent across T3–T4. Mouth-morph index list `[24,28,29,36,37,38,43,44,45,46,47,48,49,50,51]` matches `ARKIT_TO_TARGET` (T1).

**OUT (untouched):** `jarvis_agent.py` and the voice-agent brain; the production-hardening WIP; `src/cli/` (run-only).
```
