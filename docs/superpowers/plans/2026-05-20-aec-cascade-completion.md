# AEC Cascade Completion + Runtime Health-Gate — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore barge-in during TTS without JARVIS hearing its own echo, by completing the AEC cascade (retune L1, build L3) behind a runtime health-gate that keeps the mic hot only when a soak-validated-*sufficient* echo-defense set is **measured** active.

**Architecture:** PipeWire `module-echo-cancel` (L1, already loaded + the default source/sink) is the linear canceller; a new DTLN neural residual filter (L3) cleans L1's residual; the voice-client's mic-publish-during-speak gate keys on measured layer state via a new `audio/aec_health.py`, deny-by-default until a soak promotes a sufficient set. Telemetry is made truthful (probe pw-dump, not env flags). Ships behind the existing mic-drop mitigation until the soak passes.

**Tech Stack:** Python 3.13, `livekit.rtc` APM, `sounddevice`, `onnxruntime` 1.26.0, `numpy`/`scipy`, PipeWire (`pw-dump`/`pw-mon`/`wpctl` — **no `pactl` on this box**), pytest. Spec: `docs/superpowers/specs/2026-05-20-aec-cascade-completion-design.md`.

**Tests:** `cd src/voice-agent && .venv/bin/python -m pytest tests/ -q` (run the whole suite; ~25 s). Single test: `.venv/bin/python -m pytest tests/test_X.py::test_name -v`.

---

## STATUS (corrected 2026-05-20 PM — READ FIRST)

After as-built verification, most of this plan was already done or already existed:

| Task | Status |
|---|---|
| 1 — `aec_health` L1 probe | ✅ DONE (`bdc15325`) |
| 2 — EchoDefense + gate predicate | ✅ DONE (`46231574`) |
| 3 — mic-gate rewrite | ✅ DONE (`3cf2a7b5`) |
| 4 — truthful `l1_active` | ✅ DONE (`798423f3`) |
| 5 — `state.speaking` from render signal | ✅ DONE (`b93cd898`) |
| 6 — `bin/jarvis-aec-reload` | ⛔ ALREADY EXISTS (`90797a78`/`0f90f96e`) — superior + WebRTC-correct; **do NOT recreate** |
| 7 — `bin/jarvis-aec-soak` | ⛔ ALREADY EXISTS (`26d83ab5`/`0f90f96e`) — **do NOT recreate** |
| Phase A checkpoint | ⏭ NEXT — user-run live soak |
| 8–11 — L3 (DTLN) + promotion | ⏳ REMAINING — only if the soak shows tuned-L1 leaves residual |

**L1 is already loaded + WebRTC-tuned** via `~/.config/pipewire/pipewire.conf.d/99-echo-cancel.conf`. The immediate next action is the **Phase A checkpoint** (live soak), which is the user's to run. Tasks 6 & 7 below are kept for reference but are **superseded by the existing scripts** — do not overwrite them.

---

## File Structure

| File | Responsibility | Phase |
|---|---|---|
| `src/voice-agent/audio/aec_health.py` (CREATE) | L1 probe (`l1_echo_cancel_active`), `EchoDefense`, `current_echo_defense`, `sufficient_for_hot_mic` — the single source of truth for "is echo defense sufficient". | A |
| `src/voice-agent/jarvis_voice_client.py` (MODIFY) | Rewrite the mic-gate to use `aec_health`; truthful `l1_active` telemetry; `state.speaking` from the render signal; (Phase B) wire DTLN + 16 kHz publish. | A+B |
| `bin/jarvis-aec-reload` (CREATE) | Idempotent reload of `module-echo-cancel` with tuned args; verify the echo-cancel source. | A |
| `bin/jarvis-aec-soak` (CREATE) | Echo-loop detector + layer rollup from `turn_telemetry.db`; HARD-FAIL on during-speak transcripts. | A |
| `src/voice-agent/audio/dtln_aec.py` (CREATE) | `DTLNResidualFilter` — load ONNX, `process()`, latency self-disable, `healthy`. | B |
| `install.sh` (MODIFY) | Download + SHA256-verify `models/dtln_aec_128.onnx`. | B |
| `src/voice-agent/tests/test_aec_health.py` (CREATE) | Unit tests for the probe, defense, and gate predicate. | A |
| `src/voice-agent/tests/test_dtln_aec.py` (CREATE) | Unit tests for the DTLN filter (latency self-disable, passthrough on empty ref). | B |

Convention (from `tests/test_output_profile.py`): each test file starts with `import sys; from pathlib import Path; sys.path.insert(0, str(Path(__file__).parent.parent))` then `from audio import ...`; mock subprocess via `monkeypatch.setattr`.

---

# PHASE A — retune L1, health-gate, truthful telemetry, state.speaking, soak tooling

## Task 1: L1 probe — `l1_echo_cancel_active()` (pw-dump)

**Files:**
- Create: `src/voice-agent/audio/aec_health.py`
- Test: `src/voice-agent/tests/test_aec_health.py`

- [ ] **Step 1: Write the failing test**

```python
# src/voice-agent/tests/test_aec_health.py
"""Tests for the AEC health probe + gate predicate (2026-05-20)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_l1_active_true_when_default_source_is_echo_cancel(monkeypatch):
    from audio import aec_health
    monkeypatch.setattr(aec_health, "_default_source_name", lambda: "echo-cancel-source")
    monkeypatch.setenv("JARVIS_PIPEWIRE_AEC", "1")
    aec_health._l1_cache_clear()
    assert aec_health.l1_echo_cancel_active() is True


def test_l1_active_false_when_default_source_is_raw_mic(monkeypatch):
    from audio import aec_health
    monkeypatch.setattr(aec_health, "_default_source_name", lambda: "alsa_input.pci-0000_00_1f.3.analog-stereo")
    aec_health._l1_cache_clear()
    assert aec_health.l1_echo_cancel_active() is False


def test_l1_active_false_when_flag_ceiling_off(monkeypatch):
    from audio import aec_health
    monkeypatch.setattr(aec_health, "_default_source_name", lambda: "echo-cancel-source")
    monkeypatch.setenv("JARVIS_PIPEWIRE_AEC", "0")  # operator ceiling
    aec_health._l1_cache_clear()
    assert aec_health.l1_echo_cancel_active() is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_aec_health.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'audio.aec_health'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/voice-agent/audio/aec_health.py
"""Runtime AEC health: measured echo-defense state + the hot-mic gate predicate.

The 2026-05-20 echo->STT regression came from deciding "is echo defense
active" off env flags instead of runtime reality, and from trusting ANY
layer rather than a soak-validated-SUFFICIENT set. This module is the single
source of truth both the mic-gate and the telemetry consume.

This box is PipeWire-native (pw-dump/wpctl; NO pactl). Spec:
docs/superpowers/specs/2026-05-20-aec-cascade-completion-design.md
"""
from __future__ import annotations

import functools
import json
import logging
import os
import subprocess
import time

logger = logging.getLogger("jarvis.audio.aec_health")

_TTL_S = 5  # short — L1 can drop on hot-plug; the gate must see it fast.


def _default_source_name() -> str:
    """The PipeWire default.audio.source node name via pw-dump. Empty on any
    failure. Split out for test mocking."""
    try:
        raw = subprocess.run(
            ["pw-dump"], capture_output=True, text=True, timeout=2
        ).stdout
        nodes = json.loads(raw)
    except Exception:
        return ""
    if not isinstance(nodes, list):
        return ""
    for n in nodes:
        if isinstance(n, dict) and n.get("type") == "PipeWire:Interface:Metadata":
            for entry in n.get("metadata") or []:
                if entry.get("key") == "default.audio.source":
                    val = entry.get("value")
                    if isinstance(val, dict):
                        return (val.get("name") or "").strip()
                    if isinstance(val, str):
                        return val.strip()
    return ""


@functools.lru_cache(maxsize=2)
def _l1_active_cached(_ttl_bucket: int) -> bool:
    return "echo-cancel" in _default_source_name().lower()


def _l1_cache_clear() -> None:
    _l1_active_cached.cache_clear()


def l1_echo_cancel_active() -> bool:
    """True iff the active default capture source is an echo-cancel source
    (i.e. the voice-client is genuinely getting L1-cancelled mic audio).
    `JARVIS_PIPEWIRE_AEC=0` is an operator ceiling that forces it off."""
    if os.environ.get("JARVIS_PIPEWIRE_AEC", "1") != "1":
        return False
    return _l1_active_cached(int(time.time() // _TTL_S))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_aec_health.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/audio/aec_health.py src/voice-agent/tests/test_aec_health.py
git commit -m "feat(aec): L1 echo-cancel probe via pw-dump (truthful, not the flag)"
```

---

## Task 2: `EchoDefense` + `current_echo_defense` + `sufficient_for_hot_mic`

**Files:**
- Modify: `src/voice-agent/audio/aec_health.py`
- Test: `src/voice-agent/tests/test_aec_health.py`

- [ ] **Step 1: Write the failing test** (append)

```python
def test_current_echo_defense_measures_inputs(monkeypatch):
    from audio import aec_health
    monkeypatch.setattr(aec_health, "_default_source_name", lambda: "echo-cancel-source")
    monkeypatch.setenv("JARVIS_PIPEWIRE_AEC", "1")
    aec_health._l1_cache_clear()
    d = aec_health.current_echo_defense(apm_aec=False, dtln_healthy=False)
    assert d.l1 is True and d.l2_aec is False and d.l3 is False


def test_sufficient_denies_on_speakers_by_default(monkeypatch):
    from audio import aec_health
    monkeypatch.setattr(aec_health, "_HOT_MIC_SET", "none")
    d = aec_health.EchoDefense(l1=True, l2_aec=False, l3=True)
    assert aec_health.sufficient_for_hot_mic(d, "speakers") is False


def test_sufficient_headphones_always_true():
    from audio import aec_health
    d = aec_health.EchoDefense(l1=False, l2_aec=False, l3=False)
    assert aec_health.sufficient_for_hot_mic(d, "headphones") is True


def test_sufficient_l1_l3_set(monkeypatch):
    from audio import aec_health
    monkeypatch.setattr(aec_health, "_HOT_MIC_SET", "l1_l3")
    assert aec_health.sufficient_for_hot_mic(aec_health.EchoDefense(True, False, True), "speakers") is True
    assert aec_health.sufficient_for_hot_mic(aec_health.EchoDefense(True, False, False), "speakers") is False  # L1 alone insufficient


def test_current_echo_defense_failclosed(monkeypatch):
    from audio import aec_health
    def _boom():
        raise RuntimeError("pw-dump exploded")
    monkeypatch.setattr(aec_health, "l1_echo_cancel_active", _boom)
    d = aec_health.current_echo_defense(apm_aec=False, dtln_healthy=False)
    assert d.l1 is False  # exception -> measured False, never crashes the audio thread
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_aec_health.py -k "defense or sufficient" -v`
Expected: FAIL (`AttributeError: ... has no attribute 'EchoDefense'`).

- [ ] **Step 3: Write minimal implementation** (append to `aec_health.py`)

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class EchoDefense:
    l1: bool
    l2_aec: bool
    l3: bool


def current_echo_defense(*, apm_aec: bool, dtln_healthy: bool) -> EchoDefense:
    """Snapshot the MEASURED echo-defense layers. Fail-closed: any probe
    error -> that layer reads False (never raises into the audio callback)."""
    try:
        l1 = l1_echo_cancel_active()
    except Exception as e:
        logger.warning(f"[aec_health] l1 probe failed ({e}); treating as off")
        l1 = False
    return EchoDefense(l1=l1, l2_aec=bool(apm_aec), l3=bool(dtln_healthy))


# The echo-defense set proven (by `bin/jarvis-aec-soak`) sufficient to keep
# the mic hot during TTS without garbling STT. "none" until a soak promotes
# it. PROMOTE by editing this after a passing soak (spec §5.4). NEVER widen
# it on a hunch — the 2026-05-20 regression was exactly an unvalidated hot mic.
_HOT_MIC_SET = "none"   # one of: "none", "l1", "l1_l3"


def sufficient_for_hot_mic(d: EchoDefense, profile: str) -> bool:
    """True iff the validated-sufficient echo-defense set is measured active.
    Deny-by-default on speakers; headphones never have an echo path."""
    if profile == "headphones":
        return True
    if _HOT_MIC_SET == "l1":
        return d.l1
    if _HOT_MIC_SET == "l1_l3":
        return d.l1 and d.l3
    return False  # "none" -> deny -> mic-drop during speak
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_aec_health.py -v`
Expected: all passed (8 tests).

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/audio/aec_health.py src/voice-agent/tests/test_aec_health.py
git commit -m "feat(aec): EchoDefense + sufficient_for_hot_mic (deny-by-default, validated set)"
```

---

## Task 3: Rewrite the mic-gate to use measured state

**Files:**
- Modify: `src/voice-agent/jarvis_voice_client.py:156-170` (`_should_publish_during_speak`) and the call site `:813-822`.
- Test: `src/voice-agent/tests/test_aec_health.py` (gate-integration test using the rewritten signature).

Context: the current gate trusts `apm_aec or neural_aec` flags. We replace its body with `sufficient_for_hot_mic`, and change the call site to pass a measured `EchoDefense`. In Phase A there is no DTLN, so `dtln_healthy=False`.

- [ ] **Step 1: Write the failing test** (append to `test_aec_health.py`)

```python
def test_gate_drops_on_speakers_when_set_none(monkeypatch):
    """The published gate helper denies during-speak publish on speakers
    until a set is validated, regardless of L1 being present."""
    from audio import aec_health
    monkeypatch.setattr(aec_health, "_HOT_MIC_SET", "none")
    d = aec_health.EchoDefense(l1=True, l2_aec=False, l3=False)
    assert aec_health.sufficient_for_hot_mic(d, "speakers") is False
    assert aec_health.sufficient_for_hot_mic(d, "headphones") is True
```

(The gate logic now lives in `aec_health`; `jarvis_voice_client` just calls it. We test the predicate here; the client wiring is verified by Step 4's full-suite run + the existing import smoke.)

- [ ] **Step 2: Run test to verify it passes already** (predicate exists from Task 2)

Run: `.venv/bin/python -m pytest tests/test_aec_health.py::test_gate_drops_on_speakers_when_set_none -v`
Expected: PASS (this guards the behavior we're about to wire in).

- [ ] **Step 3: Rewrite `_should_publish_during_speak`** (replace `jarvis_voice_client.py:156-170`)

Replace the existing function:
```python
def _should_publish_during_speak(*, profile: str, apm_aec: bool, neural_aec: bool) -> bool:
    """2026-05-19 — replaces the blanket 'drop mic while speaking'.
    ...docstring...
    Spec 2026-05-19 §4.2 degradation ladder."""
    if profile == "headphones":
        return True
    if apm_aec or neural_aec:
        return True
    return False
```
with:
```python
def _should_publish_during_speak(*, profile: str, defense) -> bool:
    """2026-05-20 — keep the mic hot during TTS ONLY when the soak-validated
    -SUFFICIENT echo-defense set is MEASURED active (not env flags, not 'any
    layer'). L1-alone is present yet insufficient — that's the regression.
    See audio.aec_health.sufficient_for_hot_mic + the 2026-05-20 spec."""
    from audio.aec_health import sufficient_for_hot_mic
    return sufficient_for_hot_mic(defense, profile)
```

- [ ] **Step 4: Rewrite the call site** (replace `jarvis_voice_client.py:813-821`, inside `_mic_cb`)

Replace:
```python
        if state.speaking and os.environ.get("JARVIS_MIC_DURING_SPEAK", "0") != "1":
            if not _should_publish_during_speak(
                profile=_current_profile, apm_aec=_APM_AEC,
                neural_aec=(os.environ.get("JARVIS_NEURAL_AEC", "1") == "1"),
            ):
                return
```
with:
```python
        if state.speaking and os.environ.get("JARVIS_MIC_DURING_SPEAK", "0") != "1":
            from audio.aec_health import current_echo_defense
            _defense = current_echo_defense(
                apm_aec=(_apm is not None and _APM_AEC),
                dtln_healthy=False,   # Phase B sets this from the DTLN filter
            )
            if not _should_publish_during_speak(profile=_current_profile, defense=_defense):
                return
```

- [ ] **Step 5: Verify the client still imports + full suite green**

Run: `cd src/voice-agent && .venv/bin/python -c "import ast; ast.parse(open('jarvis_voice_client.py').read()); print('parse-ok')"`
Then: `.venv/bin/python -m pytest tests/ -q`
Expected: `parse-ok`, suite green (no regressions). Note: `current_echo_defense` is called inside the realtime callback — it is cached (5 s TTL) so the per-frame cost is a dict lookup, not a `pw-dump` spawn.

- [ ] **Step 6: Commit**

```bash
git add src/voice-agent/jarvis_voice_client.py src/voice-agent/tests/test_aec_health.py
git commit -m "feat(aec): mic-gate keys on measured EchoDefense, deny-by-default"
```

---

## Task 4: Truthful `l1_active` telemetry

**Files:**
- Modify: `src/voice-agent/jarvis_voice_client.py:940-953` (`_write_aec_state_snapshot`).
- Test: covered by Task 1 (the probe) + a focused snapshot test.

Context: `_write_aec_state_snapshot` currently sets `l1_active=(os.environ.get("JARVIS_PIPEWIRE_AEC","1")=="1")` (cosmetic). Make it the real probe.

- [ ] **Step 1: Replace the `l1_active` line** (`jarvis_voice_client.py:944`)

Replace:
```python
            l1_active=(os.environ.get("JARVIS_PIPEWIRE_AEC", "1") == "1"),
```
with:
```python
            l1_active=_aec_health.l1_echo_cancel_active(),
```
And add the import near the other audio imports (top of `main()`, alongside the existing `from audio.output_profile import ...` at L937):
```python
    from audio import aec_health as _aec_health
```

- [ ] **Step 2: Smoke-test the snapshot writes a bool**

Run:
```bash
cd src/voice-agent && .venv/bin/python -c "
from audio import aec_health
print('l1_active live =', aec_health.l1_echo_cancel_active())
"
```
Expected: prints `l1_active live = True` on this box (echo-cancel-source is the default). If it prints `False`, the echo-cancel module isn't the default source — run `bin/jarvis-aec-reload` (Task 6) first.

- [ ] **Step 3: Full suite green**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: green.

- [ ] **Step 4: Commit**

```bash
git add src/voice-agent/jarvis_voice_client.py
git commit -m "feat(aec): l1_active telemetry from the real pw-dump probe, not the flag"
```

---

## Task 5: `state.speaking` from the render signal (robust fallback)

**Files:**
- Modify: `src/voice-agent/jarvis_voice_client.py` — the playback path that sets `state.speaking` (the OutputStream loop near `out.write(pcm)` at L479, and the `_SPEAKING_RMS_THRESHOLD` usage L216-217).
- Test: `src/voice-agent/tests/test_speaking_signal.py` (CREATE).

Context: `state.speaking` is currently RMS-gated on the rendered track and can false-positive on ambient, muting the user when the mic-drop fallback is active. Drive it from the **outgoing Orpheus PCM** (clean, known) computed in the playback loop, with a hold.

- [ ] **Step 1: Write the failing test**

```python
# src/voice-agent/tests/test_speaking_signal.py
"""state.speaking must be driven by the OUTGOING TTS signal, not ambient."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
from audio.speaking_signal import is_rendering_speech


def test_silence_is_not_speech():
    silence = np.zeros(480, dtype=np.int16)
    assert is_rendering_speech(silence) is False


def test_loud_pcm_is_speech():
    loud = (np.ones(480, dtype=np.int16) * 8000)
    assert is_rendering_speech(loud) is True
```

- [ ] **Step 2: Run to verify fail**

Run: `.venv/bin/python -m pytest tests/test_speaking_signal.py -v`
Expected: FAIL (`No module named 'audio.speaking_signal'`).

- [ ] **Step 3: Implement** `src/voice-agent/audio/speaking_signal.py`

```python
"""Detect whether an OUTGOING TTS PCM frame is speech (vs the always-open
silent track). Used to drive state.speaking off JARVIS's own clean audio
instead of the mic-side RMS, so the mic-drop fallback never false-mutes the
user. Spec 2026-05-20 §5.5."""
from __future__ import annotations
import os
import numpy as np

# Orpheus speech sits well above this; the always-open silent track is ~0.
_SPEECH_PCM_RMS = float(os.environ.get("JARVIS_SPEAKING_PCM_RMS", "300"))


def is_rendering_speech(pcm_int16: np.ndarray) -> bool:
    if pcm_int16 is None or len(pcm_int16) == 0:
        return False
    rms = float(np.sqrt(np.mean(pcm_int16.astype(np.float32) ** 2)))
    return rms > _SPEECH_PCM_RMS
```

- [ ] **Step 4: Wire it into the playback loop** (`jarvis_voice_client.py`, where `out.write(pcm)` is at L479)

Just before `out.write(pcm)`, set the speaking flag + hold from the outgoing PCM (replaces reliance on the mic-side `_SPEAKING_RMS_THRESHOLD`):
```python
            from audio.speaking_signal import is_rendering_speech
            if is_rendering_speech(np.frombuffer(frame.data, dtype=np.int16)):
                state.speaking = True
                _speaking_until[0] = time.monotonic() + _SPEAKING_HOLD_S
            elif time.monotonic() > _speaking_until[0]:
                state.speaking = False
```
Add the `_speaking_until = [0.0]` closure cell near the playback setup (mirror the existing `_listening_until` pattern at L745). Keep `_SPEAKING_HOLD_S` (L217).

- [ ] **Step 5: Run tests + full suite**

Run: `.venv/bin/python -m pytest tests/test_speaking_signal.py tests/ -q`
Expected: green.

- [ ] **Step 6: Commit**

```bash
git add src/voice-agent/audio/speaking_signal.py src/voice-agent/jarvis_voice_client.py src/voice-agent/tests/test_speaking_signal.py
git commit -m "feat(aec): drive state.speaking from outgoing TTS PCM, not mic RMS"
```

---

## Task 6: `bin/jarvis-aec-reload` — ⛔ ALREADY EXISTS, DO NOT RECREATE

> **Superseded.** This script already exists (committed `90797a78`/`0f90f96e`) and is **better** than this task's draft: it writes a persistent PipeWire config drop-in (`~/.config/pipewire/pipewire.conf.d/99-echo-cancel.conf`) with backup/restore + pipewire restart, and is **WebRTC-correct** (NO Speex-only `filter_size_ms` — the draft below wrongly included it). L1 is already loaded + tuned via it. **Do not overwrite it**; re-run it only if the drop-in is lost. The draft below is reference only.

- [ ] **Step 1: Write the script**

```bash
#!/usr/bin/env bash
# Reload PipeWire module-echo-cancel with JARVIS's tuned args (L1 of the AEC
# cascade). Idempotent: unloads any existing instance first. Verifies the
# echo-cancel-source comes back. Spec 2026-05-20 §5.1.
set -euo pipefail

ARGS='aec.args = { webrtc.extended_filter=true webrtc.high_pass_filter=false webrtc.noise_suppression=false webrtc.gain_control=false filter_size_ms=200 } monitor.mode=true'

# Unload existing echo-cancel modules (best-effort).
if command -v pw-cli >/dev/null 2>&1; then
  for id in $(pw-cli ls Module 2>/dev/null | awk '/module-echo-cancel/{print $2}' | tr -d ',' || true); do
    pw-cli destroy "$id" 2>/dev/null || true
  done
fi

# Load tuned instance.
pw-cli -m load-module libpipewire-module-echo-cancel "$ARGS" >/dev/null 2>&1 \
  || pactl load-module module-echo-cancel "aec_args=\"webrtc.extended_filter=true filter_size_ms=200\"" >/dev/null 2>&1 \
  || { echo "jarvis-aec-reload: failed to load module-echo-cancel" >&2; exit 1; }

# Verify the echo-cancel-source exists within 3s.
for _ in $(seq 1 15); do
  if pw-dump 2>/dev/null | grep -q '"echo-cancel-source"'; then
    echo "jarvis-aec-reload: echo-cancel-source active"
    exit 0
  fi
  sleep 0.2
done
echo "jarvis-aec-reload: echo-cancel-source did NOT appear after reload" >&2
exit 1
```

- [ ] **Step 2: Make executable + smoke test**

```bash
chmod +x bin/jarvis-aec-reload
bin/jarvis-aec-reload && echo "RELOAD-OK"
```
Expected: `echo-cancel-source active` + `RELOAD-OK`. Verify the source is still the default afterward:
```bash
.venv/bin/python -c "from audio import aec_health; print('l1 active:', aec_health.l1_echo_cancel_active())" --  # run from src/voice-agent
```
Expected: `l1 active: True`. (If reload changes the default source away from echo-cancel, set it back with `wpctl set-default <echo-cancel-source-id>`; capture that in the script if needed.)

- [ ] **Step 3: Commit**

```bash
git add bin/jarvis-aec-reload
git commit -m "feat(aec): bin/jarvis-aec-reload — tuned module-echo-cancel (L1)"
```

---

## Task 7: `bin/jarvis-aec-soak` — ⛔ ALREADY EXISTS, DO NOT RECREATE

> **Superseded.** This script already exists (committed `26d83ab5`/`0f90f96e`) with self-healing column migration, output-profile distribution, layer-activation rollups, and the echo-loop detector; it takes a `[hours]` arg (default 2). **Do not overwrite it** — use it as-is for the Phase A checkpoint, e.g. `bin/jarvis-aec-soak 1`. The draft below is reference only.

- [ ] **Step 1: Write the script**

```python
#!/usr/bin/env python3
"""AEC soak rollup + echo-loop detector. Reads turn_telemetry.db over a
window and reports the during-speak STT-transcript count (must be ~0), layer
on-rates, and DTLN p95. Exit 2 on HARD-FAIL. Spec 2026-05-20 §5.4.

Usage: bin/jarvis-aec-soak [--since-min 30]
"""
import argparse, os, sqlite3, sys

DB = os.path.expanduser("~/.local/share/jarvis/turn_telemetry.db")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since-min", type=int, default=30)
    args = ap.parse_args()
    if not os.path.exists(DB):
        print(f"no telemetry db at {DB}", file=sys.stderr); return 1
    con = sqlite3.connect(DB)
    cur = con.cursor()
    since = f"-{args.since_min} minutes"
    rows = cur.execute(
        "SELECT count(*), "
        " sum(case when aec_layer1_active then 1 else 0 end), "
        " sum(case when aec_layer3_active then 1 else 0 end), "
        " max(dtln_latency_ms_p95) "
        "FROM turns WHERE ts_utc >= datetime('now', ?)", (since,),
    ).fetchone()
    n, l1n, l3n, dtln_p95 = (rows[0] or 0, rows[1] or 0, rows[2] or 0, rows[3])
    # Echo-loop: a user transcript arrived while output_profile=speakers AND
    # the mic was meant to be hot. Proxy: turns on speakers with non-empty
    # user_text whose ttfw is ~0 (echo of our own speech). We approximate with
    # a marker the agent writes; until that lands, count speaker-profile turns
    # with suspicious user_text is left to manual review. Hard signal:
    during_speak = cur.execute(
        "SELECT count(*) FROM turns WHERE ts_utc >= datetime('now', ?) "
        "AND output_profile='speakers' AND interrupted=1 AND length(user_text) > 0",
        (since,),
    ).fetchone()[0]
    print(f"window: last {args.since_min} min   turns: {n}")
    print(f"L1 on-rate: {(l1n/n*100 if n else 0):.0f}%   L3 on-rate: {(l3n/n*100 if n else 0):.0f}%")
    print(f"DTLN p95 (max): {dtln_p95}")
    print(f"during-speak/interrupted speaker turns (echo-loop proxy): {during_speak}")
    budget = float(os.environ.get("JARVIS_NEURAL_AEC_LATENCY_BUDGET_MS", "8"))
    fail = False
    if during_speak > 0:
        print("HARD-FAIL: during-speak transcripts on speakers (echo leaking)", file=sys.stderr); fail = True
    if dtln_p95 is not None and dtln_p95 > budget:
        print(f"HARD-FAIL: DTLN p95 {dtln_p95} > budget {budget}ms", file=sys.stderr); fail = True
    return 2 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Make executable + smoke run**

```bash
chmod +x bin/jarvis-aec-soak
bin/jarvis-aec-soak --since-min 60; echo "exit=$?"
```
Expected: prints the rollup; `exit=0` (or `exit=2` if echo is currently leaking — which is the signal we want).

- [ ] **Step 3: Commit**

```bash
git add bin/jarvis-aec-soak
git commit -m "feat(aec): bin/jarvis-aec-soak — echo-loop detector + layer rollup"
```

---

### Phase A checkpoint (not a code task — run before Phase B)

Run `bin/jarvis-aec-reload`, restart the voice-client (`systemctl --user restart jarvis-voice-client.service` — **check the 60 s rule first**), set `JARVIS_AEC_HOT_MIC_SET=l1` + `JARVIS_MIC_DURING_SPEAK=1` in a **soak-only** session, talk to JARVIS for ~10 min on speakers, then `bin/jarvis-aec-soak --since-min 10`. **If during-speak count ≈ 0 → tuned L1 alone is sufficient: promote `_HOT_MIC_SET="l1"`, remove the mitigation flags, STOP — Phase B is unnecessary.** If not, proceed to Phase B.

---

# PHASE B — L3 (DTLN) neural residual (only if Phase A's soak shows residual)

## Task 8: `audio/dtln_aec.py` — `DTLNResidualFilter`

**Files:**
- Create: `src/voice-agent/audio/dtln_aec.py`
- Test: `src/voice-agent/tests/test_dtln_aec.py`

> **Model I/O is artifact-dependent.** DTLN-aec ships as ONNX with model-specific input/output tensor names + LSTM state tensors. Step 1 introspects the actual model; Steps 3+ implement against what it reports. This is concrete work, not a placeholder.

- [ ] **Step 1: Download + introspect the model**

```bash
cd src/voice-agent
# (install.sh Task 9 automates this; for the build, fetch once:)
mkdir -p models
# pinned release artifact — see Task 9 for the URL + sha256
.venv/bin/python -c "
import onnxruntime as ort
s = ort.InferenceSession('models/dtln_aec_128.onnx', providers=['CPUExecutionProvider'])
print('INPUTS:',  [(i.name, i.shape, i.type) for i in s.get_inputs()])
print('OUTPUTS:', [(o.name, o.shape, o.type) for o in s.get_outputs()])
"
```
Record the printed names/shapes — they parameterize `process()` below.

- [ ] **Step 2: Write the failing test**

```python
# src/voice-agent/tests/test_dtln_aec.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
import numpy as np
from audio.dtln_aec import DTLNResidualFilter


def test_passthrough_when_model_missing(tmp_path):
    f = DTLNResidualFilter(model_path=str(tmp_path / "nope.onnx"))
    assert f.healthy is False
    mic = np.zeros(160, dtype=np.float32)
    out = f.process(mic, np.zeros(160, dtype=np.float32))
    np.testing.assert_array_equal(out, mic)   # passthrough, no crash


def test_latency_self_disable(monkeypatch):
    f = DTLNResidualFilter(model_path="", latency_budget_ms=0.0001)  # impossible budget
    f._loaded = True  # pretend loaded
    monkeypatch.setattr(f, "_infer", lambda mic, ref: mic)  # cheap infer
    for _ in range(120):
        f.process(np.zeros(160, dtype=np.float32), np.zeros(160, dtype=np.float32))
    assert f.healthy is False  # p95 over budget -> self-disabled
```

- [ ] **Step 3: Run to verify fail**

Run: `.venv/bin/python -m pytest tests/test_dtln_aec.py -v`
Expected: FAIL (`No module named 'audio.dtln_aec'`).

- [ ] **Step 4: Implement** `src/voice-agent/audio/dtln_aec.py`

```python
"""DTLN-aec neural residual filter (L3). Cleans the residual that L1's
linear AEC leaves. Single-thread onnxruntime; self-disables if per-frame
p95 latency exceeds budget. Spec 2026-05-20 §5.2.

process(mic16k, ref16k) -> np.ndarray  on 160-sample (10ms @ 16kHz) frames.
Passthrough (returns mic unchanged) whenever not healthy — never raises into
the realtime mic callback.
"""
from __future__ import annotations
import collections, logging, os, time
import numpy as np

logger = logging.getLogger("jarvis.audio.dtln_aec")
_FRAME = 160


class DTLNResidualFilter:
    def __init__(self, model_path: str | None = None, latency_budget_ms: float | None = None):
        self.model_path = model_path or os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "models", "dtln_aec_128.onnx")
        self.budget_ms = latency_budget_ms if latency_budget_ms is not None else float(
            os.environ.get("JARVIS_NEURAL_AEC_LATENCY_BUDGET_MS", "8"))
        self._loaded = False
        self._disabled = False
        self._lat = collections.deque(maxlen=100)
        self._sess = None
        self._states = None  # LSTM state tensors, shaped per Step 1's introspection
        self._load()

    def _load(self) -> None:
        if os.environ.get("JARVIS_NEURAL_AEC", "1") != "1":
            return
        try:
            import onnxruntime as ort
            so = ort.SessionOptions(); so.intra_op_num_threads = 1; so.inter_op_num_threads = 1
            self._sess = ort.InferenceSession(self.model_path, sess_options=so,
                                              providers=["CPUExecutionProvider"])
            # Initialize LSTM states to zeros per the introspected shapes.
            self._states = self._init_states()
            self._loaded = True
        except Exception as e:
            logger.warning(f"[dtln] load failed ({e}); L3 disabled (passthrough)")

    def _init_states(self):
        # Shapes come from Step 1's get_inputs() (the non-audio inputs).
        # Fill with the model's documented zero-states.
        return {}

    @property
    def healthy(self) -> bool:
        return self._loaded and not self._disabled

    def _infer(self, mic16k: np.ndarray, ref16k: np.ndarray) -> np.ndarray:
        # Build feeds from mic/ref + carried states per Step 1's input names;
        # run; carry the returned states; return the cleaned audio output.
        feeds = self._build_feeds(mic16k, ref16k)
        outs = self._sess.run(None, feeds)
        self._update_states(outs)
        return self._audio_out(outs).astype(np.float32)

    def _build_feeds(self, mic16k, ref16k):  # parameterized by Step 1
        raise NotImplementedError("fill from get_inputs() names in Step 1")

    def _update_states(self, outs):  # parameterized by Step 1
        pass

    def _audio_out(self, outs):  # parameterized by Step 1
        return outs[0].reshape(-1)[:_FRAME]

    def process(self, mic16k: np.ndarray, ref16k: np.ndarray) -> np.ndarray:
        if not self.healthy:
            return mic16k
        t0 = time.perf_counter()
        try:
            out = self._infer(mic16k, ref16k)
        except Exception as e:
            logger.warning(f"[dtln] infer failed ({e}); disabling L3 for session")
            self._disabled = True
            return mic16k
        self._lat.append((time.perf_counter() - t0) * 1000.0)
        if len(self._lat) >= 100:
            p95 = float(np.percentile(np.array(self._lat), 95))
            if p95 > self.budget_ms:
                logger.warning(f"[dtln] p95 {p95:.1f}ms > budget {self.budget_ms}ms; disabling L3")
                self._disabled = True
            self.p95_ms = p95
        return out

    p95_ms: float | None = None
```

> Step 4 follow-up: replace `_build_feeds`/`_update_states`/`_audio_out` `NotImplementedError`/stubs with the concrete tensor wiring from Step 1's introspection. The two passthrough/latency tests must pass without the real model (they exercise the not-healthy + self-disable paths); a third test against the real model is added once the artifact is present.

- [ ] **Step 5: Run tests to pass**

Run: `.venv/bin/python -m pytest tests/test_dtln_aec.py -v`
Expected: 2 passed (passthrough + self-disable; both run without the real model).

- [ ] **Step 6: Commit**

```bash
git add src/voice-agent/audio/dtln_aec.py src/voice-agent/tests/test_dtln_aec.py
git commit -m "feat(aec): DTLNResidualFilter (L3) — latency self-disable, fail-safe passthrough"
```

---

## Task 9: Model download in `install.sh`

**Files:**
- Modify: `install.sh` (near `install_echo_cancel_aec`, L443-450) — add `install_dtln_model`.

- [ ] **Step 1: Add the download function + call**

```bash
install_dtln_model() {
  local DEST="$INSTALL_DIR/src/voice-agent/models/dtln_aec_128.onnx"
  local URL="https://github.com/breizhn/DTLN-aec/releases/download/v1.0/dtln_aec_128.onnx"  # pin to the release you validated
  local SHA="<sha256-of-the-validated-artifact>"   # fill from `sha256sum` of the file you tested
  mkdir -p "$(dirname "$DEST")"
  if [ -f "$DEST" ] && echo "$SHA  $DEST" | sha256sum -c - >/dev/null 2>&1; then
    return 0
  fi
  curl -fsSL "$URL" -o "$DEST.tmp" || { echo "dtln model download failed" >&2; return 1; }
  if ! echo "$SHA  $DEST.tmp" | sha256sum -c - >/dev/null 2>&1; then
    echo "dtln model sha256 mismatch — refusing" >&2; rm -f "$DEST.tmp"; return 1
  fi
  mv "$DEST.tmp" "$DEST"
}
```
Add `install_dtln_model` next to the existing `install_echo_cancel_aec` call (L716).

> The `<sha256-...>` is filled with the actual `sha256sum models/dtln_aec_128.onnx` of the artifact validated in Task 8 Step 1 — this is a concrete value recorded during the build, not a placeholder left for later.

- [ ] **Step 2: Run it**

```bash
bash -c 'INSTALL_DIR=/home/ulrich/Documents/Projects/jarvis; source <(sed -n "/install_dtln_model()/,/^}/p" install.sh); install_dtln_model && echo OK'
```
Expected: `OK`; the model exists + verifies.

- [ ] **Step 3: Commit**

```bash
git add install.sh
git commit -m "feat(aec): install.sh downloads + sha256-verifies the DTLN model"
```

---

## Task 10: Wire DTLN into the mic path + l3 + 16 kHz publish

**Files:**
- Modify: `src/voice-agent/jarvis_voice_client.py` — instantiate `_dtln`, call it in `_mic_cb` after APM, set `dtln_healthy` in the gate call (Task 3 Step 4) and in `_write_aec_state_snapshot`; switch `SAMPLE_RATE` publish to 16 kHz (with the 48 k fallback).

- [ ] **Step 1: Instantiate the filter** (near the reverse-stream setup, L141-143)

```python
from audio.dtln_aec import DTLNResidualFilter
_dtln = DTLNResidualFilter()
```

- [ ] **Step 2: Call it in `_mic_cb`** after `process_stream` (around L783), speakers-only:

```python
        if _dtln.healthy and _current_profile == "speakers":
            try:
                adc_t2 = _time.inputBufferAdcTime
            except Exception:
                adc_t2 = time.monotonic()
            mic16 = np.frombuffer(frame.data, dtype=np.int16).astype(np.float32) / 32768.0
            ref16 = _reverse_ringbuf.read_16k_aligned(adc_t2)
            cleaned = _dtln.process(_resample_48k_to_16k(mic16), ref16)
            frame = rtc.AudioFrame(
                data=(np.clip(cleaned, -1, 1) * 32767).astype(np.int16).tobytes(),
                sample_rate=16000, num_channels=NUM_CHANNELS, samples_per_channel=len(cleaned),
            )
```
(Use the chosen publish format — see Step 4. `_resample_48k_to_16k` = `scipy.signal.resample_poly(x, 1, 3)`.)

- [ ] **Step 3: Set `dtln_healthy` truthfully** — update the Task 3 Step 4 gate call and `_write_aec_state_snapshot`:
```python
            _defense = current_echo_defense(
                apm_aec=(_apm is not None and _APM_AEC),
                dtln_healthy=_dtln.healthy,
            )
```
and in `_write_aec_state_snapshot`: `l3_active=_dtln.healthy, dtln_latency_ms_p95=_dtln.p95_ms,`.

- [ ] **Step 4: Mic-publish format** — validate 16 kHz end-to-end:

```bash
# After wiring, restart the client (60s rule!) and confirm the agent receives
# intelligible 16k audio (a clean transcript of a test phrase):
sqlite3 ~/.local/share/jarvis/turn_telemetry.db "SELECT user_text FROM turns ORDER BY id DESC LIMIT 1;"
```
Expected: a correct transcript. If the SFU/agent rejects 16 kHz (garbled/empty), use **fallback 2a**: keep `SAMPLE_RATE=48000` publish and upsample DTLN's 16 k output back to 48 k (`resample_poly(cleaned, 3, 1)`) instead of changing the track rate.

- [ ] **Step 5: Tests + suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: green.

- [ ] **Step 6: Commit**

```bash
git add src/voice-agent/jarvis_voice_client.py
git commit -m "feat(aec): wire DTLN (L3) into mic path + l3 telemetry + 16k publish"
```

---

## Task 11: Validate + promote + remove the mitigation (rollout)

**Files:**
- Modify: `src/voice-agent/audio/aec_health.py` (`_HOT_MIC_SET`), `~/.config/systemd/user/jarvis-voice-client.service.d/override.conf` + `jarvis-voice-client.service`, `setup/systemd/jarvis-voice-client.service`.

- [ ] **Step 1: Soak with the candidate set forced hot**

Restart the client (60 s rule), set `JARVIS_AEC_HOT_MIC_SET=l1_l3` + `JARVIS_MIC_DURING_SPEAK=1` for a soak session, talk ~30 min on speakers, then:
```bash
bin/jarvis-aec-soak --since-min 30; echo "exit=$?"
```
Expected: `exit=0` (0 during-speak transcripts, DTLN p95 < budget). If `exit=2`, do NOT promote — investigate (tune L1 args / DTLN).

- [ ] **Step 2: Promote the validated set** (`aec_health.py`)
```python
_HOT_MIC_SET = "l1_l3"   # promoted 2026-MM-DD after a passing 30-min soak (exit 0)
```

- [ ] **Step 3: Remove the mitigation flags** — delete `JARVIS_NEURAL_AEC=0` (voice-client unit) and `JARVIS_MIC_DURING_SPEAK=0` (override.conf), since the gate now safely allows hot-mic. Keep `JARVIS_NEURAL_AEC` unset (code default 1 = L3 on). `daemon-reload` + restart the client.

- [ ] **Step 4: Confirm barge-in + no echo live**

Say a long reply trigger, then interrupt with "stop" mid-reply → JARVIS stops < 500 ms. Then `bin/jarvis-aec-soak --since-min 5` → `exit=0`.

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/audio/aec_health.py setup/systemd/jarvis-voice-client.service
git commit -m "feat(aec): promote l1_l3 hot-mic set + remove echo mitigation (soak-validated)"
```

---

## Self-Review (against the spec)

**Spec coverage:** ① → Tasks 1,4,6. ② → Tasks 8,9,10. ③ → Tasks 2,3 (`current_echo_defense` + `sufficient_for_hot_mic`, deny-by-default, fail-closed). ④ → Tasks 7,11 (soak + soak-gated rollout). ⑤ → Task 5. Phasing (A then B, soak arbitrates) → Phase A checkpoint + Task 11. Truthful telemetry (G3) → Tasks 1,4,10. All spec requirements map to a task.

**Placeholder scan:** The only deferred specifics are (a) the DTLN model tensor I/O (Task 8 introspects the real artifact in Step 1, then wires it — concrete work, not hand-waving) and (b) the model URL/SHA in Task 9 (filled from the validated artifact during the build). Both are explicitly resolved within their tasks, not left open.

**Type consistency:** `EchoDefense(l1,l2_aec,l3)` is constructed identically in Tasks 2,3,10. `sufficient_for_hot_mic(d, profile)` and `current_echo_defense(apm_aec=, dtln_healthy=)` signatures match across Tasks 2,3,10. `_HOT_MIC_SET` values (`none|l1|l1_l3`) consistent in Tasks 2,11. `DTLNResidualFilter.healthy`/`.p95_ms`/`.process` consistent in Tasks 8,10. `l1_echo_cancel_active()` consistent in Tasks 1,4.
