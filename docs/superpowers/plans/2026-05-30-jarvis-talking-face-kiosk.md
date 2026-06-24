# JARVIS Talking Face (kiosk) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the kiosk WebGL ring with a live 3D FaceCap head rendered in Blender that opens its mouth in proportion to JARVIS's voice loudness, captured into the kiosk over a localhost MJPEG stream, with the ring as an automatic fallback.

**Architecture:** A Python sidecar (evolving `animators/blender_face.py`) reads JARVIS's speaking gate (log tail) + a PipeWire loudness tap, computes `jawOpen` + co-articulation, and writes ARKit shape keys to a FaceCap head over the Blender MCP socket (`:9876`). A code module injected into Blender renders the face camera offscreen ~30 fps and serves frames as MJPEG on `127.0.0.1:8770`. The kiosk's `KioskHUD` swaps `<AgentAudioVisualizerAura>` for a `<FaceStream>` `<img>` element, falling back to the ring when the stream is down.

**Tech Stack:** Python 3.13 (voice-agent `.venv`), Blender Python (`bpy`, `gpu`, `numpy`), PipeWire/`parec`, React (Tauri desktop, Vite), Blender MCP addon socket.

**Spec:** `docs/superpowers/specs/2026-05-30-jarvis-talking-face-kiosk-design.md`

---

## Constraints carried from the codebase (do not violate)

- **Voice-agent core is OUT of scope.** No edits to `jarvis_agent.py` or `jarvis_voice_client.py`. Loudness comes from a PipeWire monitor, not a PCM tap.
- **Static visualization only in the kiosk** (`.claude/rules/desktop-tauri.md`): the MJPEG `<img>` refreshes natively — do NOT introduce per-frame React `setState`. `faceOk` flips only on stream failure/recovery.
- **System-tray indicator is FROZEN** — untouched here.
- **Tests:** `cd src/voice-agent && .venv/bin/python -m pytest tests/`. Desktop: `cd src/voice-agent/desktop-tauri && npm run build`; installed kiosk needs `cargo build --release` to re-embed `dist/`.
- **No Co-Authored-By / attribution trailers** on commits.
- The animator's ARKit indices are **hints**; resolve by shape-key **name** against the live `key_blocks` (spec R1).

---

## File structure

| File | New/Mod | Responsibility |
|---|---|---|
| `src/voice-agent/animators/face_anim_core.py` | **New** | Pure animation math: `target_jaw`, `smooth_jaw`, `shape_values`. No bpy/IO. Fully unit-tested. |
| `src/voice-agent/animators/loudness_monitor.py` | **New** | `rms_level()` (pure) + `LoudnessMonitor` (PipeWire `parec` tap with injectable frame source) + `default_monitor_device()`. |
| `src/voice-agent/animators/blender_scene_setup.py` | **New** | `CODE` string + `install(conn)` — injected into Blender: import FaceCap, rename `FaceCap_Head`, assert 52 shapes, add `JarvisFaceCam`, lights, dark world. Idempotent. |
| `src/voice-agent/animators/blender_frame_server.py` | **New** | `CODE` string + `install(conn)` — injected into Blender: offscreen render of `JarvisFaceCam` → JPEG to `/dev/shm` (atomic) + threaded MJPEG HTTP server on `:8770`. Idempotent. |
| `src/voice-agent/animators/blender_face.py` | **Mod** | Orchestrator: connect → install scene + frame server → resolve shape indices by name → loop: gate × loudness → shape keys. |
| `src/voice-agent/tests/test_face_anim_core.py` | **New** | Unit tests for the pure math. |
| `src/voice-agent/tests/test_loudness_monitor.py` | **New** | Unit tests for `rms_level` + `LoudnessMonitor` with a fake frame source. |
| `bin/jarvis-face-animator` | **New** | Launcher: runs the animator in the voice-agent `.venv`. |
| `src/voice-agent/desktop-tauri/src/components/FaceStream.jsx` | **New** | MJPEG `<img>` + health callback. |
| `src/voice-agent/desktop-tauri/src/components/KioskHUD.jsx` | **Mod** | Render `<FaceStream>`; ring fallback when `!faceOk`. |
| `src/voice-agent/desktop-tauri/src-tauri/tauri.conf.json` | **Mod** | CSP `img-src` += `http://127.0.0.1:8770`. |

**Phasing:** Phase 1 (Tasks 1–2) pure TDD, no Blender needed. Phase 2 (Tasks 3–4) Blender-injected code, verified live via the MCP connection + curl. Phase 3 (Task 5) wires the orchestrator end-to-end. Phase 4 (Tasks 6–7) kiosk + final verification.

---

## Task 1: Pure animation math (`face_anim_core.py`)

**Files:**
- Create: `src/voice-agent/animators/face_anim_core.py`
- Test: `src/voice-agent/tests/test_face_anim_core.py`

- [ ] **Step 1: Write the failing test**

Create `src/voice-agent/tests/test_face_anim_core.py`:

```python
import math
from animators import face_anim_core as fac


def test_target_jaw_zero_when_not_speaking():
    assert fac.target_jaw(False, 1.0, gain=4.0) == 0.0


def test_target_jaw_tracks_level_with_gain_and_clamp():
    # level 0.1 * gain 4 = 0.4
    assert fac.target_jaw(True, 0.1, gain=4.0) == 0.4
    # clamps to max_jaw
    assert fac.target_jaw(True, 1.0, gain=4.0, max_jaw=1.0) == 1.0
    # never negative
    assert fac.target_jaw(True, 0.0, gain=4.0) == 0.0


def test_smooth_jaw_opens_faster_than_it_closes():
    # opening: current 0 -> target 1 with attack 0.5
    assert fac.smooth_jaw(0.0, 1.0, attack=0.5, decay=0.1) == 0.5
    # closing: current 1 -> target 0 with decay 0.1
    assert math.isclose(fac.smooth_jaw(1.0, 0.0, attack=0.5, decay=0.1), 0.9)


def test_shape_values_co_articulation():
    v = fac.shape_values(0.0)
    assert v["jawOpen"] == 0.0
    assert v["mouthClose"] == 1.0          # fully closed at rest
    v = fac.shape_values(1.0)
    assert v["jawOpen"] == 1.0
    assert v["mouthClose"] == 0.0          # 1 - 1*1.5 clamped to 0
    assert math.isclose(v["mouthFunnel"], 0.25)
    assert math.isclose(v["mouthPucker"], 0.10)


def test_shape_values_clamps_input():
    assert fac.shape_values(5.0)["jawOpen"] == 1.0
    assert fac.shape_values(-5.0)["jawOpen"] == 0.0
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_face_anim_core.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'animators.face_anim_core'`.

- [ ] **Step 3: Write the minimal implementation**

Create `src/voice-agent/animators/face_anim_core.py`:

```python
"""Pure animation math for the JARVIS talking face.

No bpy, no IO, no threads — every function here is deterministic and unit
tested. The orchestrator (blender_face.py) composes these with the loudness
monitor and the Blender socket.

ARKit shape-key NAME -> default index hint (FaceCap / standard 52-shape order).
The orchestrator resolves real indices by name against the live key_blocks;
these are only fallback hints.
"""

ARKIT_INDEX_HINTS = {
    "jawOpen": 17,
    "mouthClose": 18,
    "mouthFunnel": 19,
    "mouthPucker": 20,
}


def target_jaw(speaking: bool, level: float, gain: float = 4.0,
               max_jaw: float = 1.0) -> float:
    """Desired jaw openness for this frame.

    While speaking, jaw tracks loudness (level 0..1) scaled by gain and
    clamped to [0, max_jaw]. While not speaking, jaw target is 0.
    """
    if not speaking:
        return 0.0
    return max(0.0, min(max_jaw, gain * level))


def smooth_jaw(current: float, target: float,
               attack: float = 0.20, decay: float = 0.12) -> float:
    """One asymmetric smoothing step: opens (attack) faster than it closes
    (decay), which reads as natural speech."""
    smoothing = attack if target > current else decay
    return current + (target - current) * smoothing


def shape_values(jaw: float) -> dict:
    """Map a 0..1 jaw openness to ARKit shape-key values with light
    co-articulation so the mouth shuts cleanly at rest."""
    jaw = max(0.0, min(1.0, jaw))
    return {
        "jawOpen": jaw,
        "mouthClose": max(0.0, 1.0 - jaw * 1.5),
        "mouthFunnel": jaw * 0.25,
        "mouthPucker": jaw * 0.10,
    }
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_face_anim_core.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/animators/face_anim_core.py src/voice-agent/tests/test_face_anim_core.py
git commit -m "feat(face): pure jaw/co-articulation math for talking face"
```

---

## Task 2: Loudness monitor (`loudness_monitor.py`)

**Files:**
- Create: `src/voice-agent/animators/loudness_monitor.py`
- Test: `src/voice-agent/tests/test_loudness_monitor.py`

- [ ] **Step 1: Write the failing test**

Create `src/voice-agent/tests/test_loudness_monitor.py`:

```python
import struct
import time
from animators import loudness_monitor as lm


def _pcm(amplitude: int, n: int = 512) -> bytes:
    """n signed-16 mono samples at a constant amplitude."""
    return struct.pack(f"<{n}h", *([amplitude] * n))


def test_rms_level_silence_is_zero():
    assert lm.rms_level(_pcm(0), gain=1.0, floor=0.0) == 0.0
    assert lm.rms_level(b"", gain=1.0, floor=0.0) == 0.0


def test_rms_level_full_scale_clamps_to_one():
    # amplitude near max (32767) with gain 1 -> ~1.0, clamped
    assert lm.rms_level(_pcm(32767), gain=1.0, floor=0.0) == 1.0


def test_rms_level_floor_subtracts():
    # tiny signal below floor -> 0
    assert lm.rms_level(_pcm(50), gain=1.0, floor=0.5) == 0.0


def test_monitor_reports_level_from_injected_frames():
    frames = [_pcm(20000), _pcm(20000), _pcm(20000)]
    it = iter(frames)

    def source():
        try:
            return next(it)
        except StopIteration:
            return b""  # end-of-stream

    mon = lm.LoudnessMonitor(frame_source=source, gain=2.0, floor=0.0,
                             ema=1.0)  # ema=1 -> no smoothing, instant
    mon.start()
    time.sleep(0.05)
    assert mon.level() > 0.5
    mon.stop()


def test_monitor_degrades_when_source_unavailable():
    def source():
        raise RuntimeError("no pipewire here")

    mon = lm.LoudnessMonitor(frame_source=source, degraded_level=0.7)
    mon.start()
    time.sleep(0.05)
    # falls back to the degraded constant so the jaw still moves
    assert mon.level() == 0.7
    mon.stop()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_loudness_monitor.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'animators.loudness_monitor'`.

- [ ] **Step 3: Write the minimal implementation**

Create `src/voice-agent/animators/loudness_monitor.py`:

```python
"""Real-time loudness of JARVIS's voice via a PipeWire monitor tap.

`rms_level` is pure and unit-tested. `LoudnessMonitor` runs a background
thread reading signed-16 mono PCM chunks from a frame source (by default a
`parec` subprocess on the output sink's .monitor) and exposes a smoothed
0..1 level. The frame source is injectable for testing.
"""

import math
import os
import shutil
import struct
import subprocess
import threading


def rms_level(pcm_s16: bytes, gain: float = 4.0, floor: float = 0.004) -> float:
    """RMS of signed-16 mono PCM -> normalized 0..1 level.

    rms is divided by 32768 to land in 0..1, the noise floor is subtracted,
    the result scaled by gain and clamped.
    """
    n = len(pcm_s16) // 2
    if n == 0:
        return 0.0
    samples = struct.unpack(f"<{n}h", pcm_s16[: n * 2])
    mean_sq = sum(s * s for s in samples) / n
    rms = math.sqrt(mean_sq) / 32768.0
    level = (rms - floor) * gain
    return max(0.0, min(1.0, level))


def default_monitor_device() -> str:
    """Best-effort name of the default sink's monitor source.

    Honors $JARVIS_FACE_AUDIO_MONITOR; otherwise asks pactl for the default
    sink and appends '.monitor'. Returns '' if it can't be determined (the
    caller then lets parec pick the default source).
    """
    override = os.getenv("JARVIS_FACE_AUDIO_MONITOR")
    if override:
        return override
    if shutil.which("pactl"):
        try:
            sink = subprocess.check_output(
                ["pactl", "get-default-sink"], text=True, timeout=2
            ).strip()
            if sink:
                return f"{sink}.monitor"
        except Exception:
            pass
    return ""


# bytes per read: ~512 samples * 2 bytes
_CHUNK = 1024


def _parec_source(device: str):
    """Return a callable yielding PCM chunks from a parec subprocess."""
    cmd = ["parec", "--format=s16le", "--rate=16000", "--channels=1"]
    if device:
        cmd += [f"--device={device}"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL)

    def read():
        if proc.stdout is None:
            return b""
        return proc.stdout.read(_CHUNK)

    read._proc = proc  # type: ignore[attr-defined]
    return read


class LoudnessMonitor:
    """Background RMS reader exposing a smoothed 0..1 level()."""

    def __init__(self, frame_source=None, gain: float = 6.0,
                 floor: float = 0.004, ema: float = 0.4,
                 degraded_level: float = 0.75):
        self._frame_source = frame_source  # callable()->bytes, or None
        self._gain = gain
        self._floor = floor
        self._ema = ema  # 1.0 = no smoothing
        self._degraded_level = degraded_level
        self._level = 0.0
        self._degraded = False
        self._running = False
        self._thread = None
        self._lock = threading.Lock()

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)

    def level(self) -> float:
        with self._lock:
            if self._degraded:
                return self._degraded_level
            return self._level

    def _ensure_source(self):
        if self._frame_source is not None:
            return self._frame_source
        device = default_monitor_device()
        self._frame_source = _parec_source(device)
        return self._frame_source

    def _run(self):
        try:
            source = self._ensure_source()
        except Exception:
            with self._lock:
                self._degraded = True
            return
        while self._running:
            try:
                chunk = source()
            except Exception:
                with self._lock:
                    self._degraded = True
                return
            if not chunk:
                # end-of-stream / no data: brief idle, keep last level
                continue
            lvl = rms_level(chunk, gain=self._gain, floor=self._floor)
            with self._lock:
                self._level = (self._ema * lvl
                               + (1.0 - self._ema) * self._level)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_loudness_monitor.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/animators/loudness_monitor.py src/voice-agent/tests/test_loudness_monitor.py
git commit -m "feat(face): PipeWire loudness monitor with injectable source"
```

---

## Task 3: Blender scene setup (`blender_scene_setup.py`)

This installs the renderable scene into the running Blender. It is verified **live** against the actual Blender (via the MCP socket), not with pytest — Blender's API is version-sensitive and `bpy` isn't importable outside Blender.

**Files:**
- Create: `src/voice-agent/animators/blender_scene_setup.py`

- [ ] **Step 1: Write the module**

Create `src/voice-agent/animators/blender_scene_setup.py`:

```python
"""Scene setup injected into Blender over the MCP socket.

Imports the FaceCap ARKit head, renames its mesh to 'FaceCap_Head' (the name
the animator drives), asserts the ARKit shape keys exist, and builds a camera
+ portrait lighting + dark world tuned to the kiosk palette. Idempotent: safe
to re-run; it won't duplicate the camera/lights and only imports the head once.

This file holds CODE as a string because it executes inside Blender's Python,
not the voice-agent venv. `install(conn)` sends it via blender_face.BlenderConnection.
"""

FACECAP_UID = "29c2a506582a4157bf970bb8721a970c"

CODE = r'''
import bpy

CYAN = (0.122, 0.835, 0.976)  # #1FD5F9 linear-ish

def _ensure_head():
    head = bpy.data.objects.get("FaceCap_Head")
    if head and head.type == "MESH" and head.data.shape_keys:
        return head, "exists"
    # find a mesh with ARKit-ish shape keys among recently imported objects
    for o in bpy.data.objects:
        if o.type == "MESH" and o.data.shape_keys:
            kb = o.data.shape_keys.key_blocks
            names = {k.name for k in kb}
            if "jawOpen" in names:
                o.name = "FaceCap_Head"
                return o, "renamed"
    return None, "missing"

def _ensure_camera():
    cam = bpy.data.objects.get("JarvisFaceCam")
    if cam is None:
        cam_data = bpy.data.cameras.new("JarvisFaceCam")
        cam = bpy.data.objects.new("JarvisFaceCam", cam_data)
        bpy.context.scene.collection.objects.link(cam)
    head = bpy.data.objects.get("FaceCap_Head")
    if head:
        # frame the head: place camera in front along -Y, slightly up
        import mathutils
        c = head.matrix_world.translation
        dim = head.dimensions
        dist = max(dim.x, dim.z) * 3.2
        cam.location = (c.x, c.y - dist, c.z + dim.z * 0.15)
        cam.rotation_euler = (1.5708, 0.0, 0.0)  # look along +Y toward the face
        cam.data.lens = 50
    bpy.context.scene.camera = cam
    return cam

def _ensure_light(name, kind, loc, energy, color=(1,1,1)):
    obj = bpy.data.objects.get(name)
    if obj is None:
        ld = bpy.data.lights.new(name, kind)
        obj = bpy.data.objects.new(name, ld)
        bpy.context.scene.collection.objects.link(obj)
    obj.data.energy = energy
    obj.data.color = color
    obj.location = loc
    return obj

def _dark_world():
    scn = bpy.context.scene
    world = bpy.data.worlds.get("JarvisFaceWorld")
    if world is None:
        world = bpy.data.worlds.new("JarvisFaceWorld")
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs[0].default_value = (0.01, 0.01, 0.015, 1.0)
        bg.inputs[1].default_value = 0.15
    scn.world = world

def setup():
    head, status = _ensure_head()
    if head is None:
        print("RESULT: NO_HEAD")
        return
    # hide the female sculpt if present (don't delete)
    for o in bpy.data.objects:
        if o.name.startswith("Object_") or o.name in ("Sketchfab_model", "Geode"):
            if o is not head:
                o.hide_render = True
                o.hide_viewport = True
    # smooth-shade the head
    if head.type == "MESH":
        for p in head.data.polygons:
            p.use_smooth = True
    _ensure_camera()
    _ensure_light("JarvisKey",  "AREA", ( 1.2, -1.6, 1.2), 800.0)
    _ensure_light("JarvisFill", "AREA", (-1.4, -1.2, 0.6), 250.0)
    _ensure_light("JarvisRim",  "AREA", ( 0.0,  1.6, 1.4), 600.0, CYAN)
    _dark_world()
    # report the resolved ARKit shape-key indices by name
    kb = head.data.shape_keys.key_blocks
    idx = {}
    for want in ("jawOpen", "mouthClose", "mouthFunnel", "mouthPucker"):
        for i, k in enumerate(kb):
            if k.name == want:
                idx[want] = i
                break
    print("RESULT: OK status=%s shapes=%d indices=%s" % (status, len(kb), idx))

setup()
'''


def install(conn):
    """Send the scene-setup CODE into Blender via a BlenderConnection.

    `conn` is an animators.blender_face.BlenderConnection. Returns the raw
    result dict from the MCP addon (contains the printed RESULT line).
    """
    return conn.send("execute_code", {"code": CODE})
```

- [ ] **Step 2: Import the FaceCap head into Blender (one-time)**

Using the Blender MCP `download_sketchfab_model` tool, import uid `29c2a506582a4157bf970bb8721a970c` at `target_size=0.24`. (During implementation this is a single MCP tool call; the head ships with the 52 ARKit shape keys.)

Expected: import succeeds; a mesh with shape key `jawOpen` exists.

- [ ] **Step 3: Run the setup live and verify**

Send `blender_scene_setup.CODE` via the MCP `execute_blender_code` tool (or `install(conn)` once Task 5's connection exists).

Expected printed output contains: `RESULT: OK status=... shapes=52 indices={'jawOpen': 17, 'mouthClose': 18, 'mouthFunnel': 19, 'mouthPucker': 20}` (index numbers may differ — that's why the orchestrator resolves by name). If `RESULT: NO_HEAD`, the import in Step 2 failed — fix before continuing.

- [ ] **Step 4: Visual check**

Capture a viewport screenshot (MCP `get_viewport_screenshot`). Confirm the head is framed by `JarvisFaceCam`, lit (key/fill + cyan rim), on a near-black background, mouth closed. Adjust `_ensure_camera` distances/lens or light energies inline if framing/exposure is off, re-run Step 3.

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/animators/blender_scene_setup.py
git commit -m "feat(face): Blender scene setup — FaceCap head, camera, kiosk-palette lighting"
```

---

## Task 4: Blender frame server (`blender_frame_server.py`)

Injected into Blender: renders `JarvisFaceCam` offscreen ~30 fps to a JPEG in `/dev/shm` (atomic rename) and serves it as MJPEG on `127.0.0.1:8770`. Verified live with `curl`.

**Files:**
- Create: `src/voice-agent/animators/blender_frame_server.py`

- [ ] **Step 1: Write the module**

Create `src/voice-agent/animators/blender_frame_server.py`:

```python
"""MJPEG frame server injected into Blender.

A bpy.app.timers callback (main thread, required for GPU) renders the face
camera offscreen, writes a JPEG to /dev/shm via atomic rename, and a daemon
HTTP thread serves it as multipart/x-mixed-replace on 127.0.0.1:8770.

Idempotent: a guard flag in bpy.app.driver_namespace prevents double-install
of the timer + server across re-runs (e.g. animator restarts).

Endpoints:
  GET /stream.mjpg  -> multipart/x-mixed-replace MJPEG
  GET /frame.jpg    -> single latest JPEG (health/smoke test)
"""

CODE = r'''
import bpy, gpu, os, threading, time
import numpy as np
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

NS = bpy.app.driver_namespace
PORT = int(os.environ.get("JARVIS_FACE_PORT", "8770"))
W = int(os.environ.get("JARVIS_FACE_W", "384"))
H = int(os.environ.get("JARVIS_FACE_H", "384"))
FPS = float(os.environ.get("JARVIS_FACE_FPS", "30"))
SHM = "/dev/shm/jarvis_face.jpg"
TMP = "/dev/shm/jarvis_face.tmp.jpg"

def _find_view3d():
    for area in bpy.context.screen.areas:
        if area.type == "VIEW_3D":
            region = next((r for r in area.regions if r.type == "WINDOW"), None)
            return area.spaces.active, region
    return None, None

def _render_to_shm():
    scene = bpy.context.scene
    cam = bpy.data.objects.get("JarvisFaceCam")
    if cam is None:
        return
    space, region = _find_view3d()
    if space is None or region is None:
        return
    offscreen = NS.get("jarvis_off")
    if offscreen is None:
        offscreen = gpu.types.GPUOffScreen(W, H)
        NS["jarvis_off"] = offscreen
    view_matrix = cam.matrix_world.inverted()
    projection_matrix = cam.calc_matrix_camera(
        bpy.context.evaluated_depsgraph_get(), x=W, y=H)
    offscreen.draw_view3d(
        scene, bpy.context.view_layer, space, region,
        view_matrix, projection_matrix, do_color_management=True)
    with offscreen.bind():
        fb = gpu.state.active_framebuffer_get()
        buf = fb.read_color(0, 0, W, H, 4, 0, "UBYTE")
    buf.dimensions = W * H * 4
    arr = np.frombuffer(bytes(buf), dtype=np.uint8).astype(np.float32) / 255.0
    # write into a reusable bpy image, save JPEG, atomic-rename
    img = bpy.data.images.get("JarvisFaceFrame")
    if img is None or tuple(img.size) != (W, H):
        if img:
            bpy.data.images.remove(img)
        img = bpy.data.images.new("JarvisFaceFrame", width=W, height=H)
    img.pixels.foreach_set(arr)
    img.file_format = "JPEG"
    img.filepath_raw = TMP
    img.save()
    os.replace(TMP, SHM)

def _timer():
    try:
        _render_to_shm()
    except Exception as e:
        print("[face-server] render error:", e)
    return 1.0 / FPS

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass
    def _latest(self):
        try:
            with open(SHM, "rb") as f:
                return f.read()
        except OSError:
            return None
    def do_GET(self):
        if self.path.startswith("/frame.jpg"):
            data = self._latest()
            if not data:
                self.send_response(503); self.end_headers(); return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers(); self.wfile.write(data); return
        if self.path.startswith("/stream.mjpg"):
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Type",
                "multipart/x-mixed-replace; boundary=frame")
            self.end_headers()
            try:
                while True:
                    data = self._latest()
                    if data:
                        self.wfile.write(b"--frame\r\n")
                        self.wfile.write(b"Content-Type: image/jpeg\r\n")
                        self.wfile.write(
                            ("Content-Length: %d\r\n\r\n" % len(data)).encode())
                        self.wfile.write(data); self.wfile.write(b"\r\n")
                    time.sleep(1.0 / FPS)
            except (BrokenPipeError, ConnectionResetError):
                return
            return
        self.send_response(404); self.end_headers()

def _install():
    if NS.get("jarvis_face_installed"):
        print("RESULT: ALREADY_INSTALLED")
        return
    bpy.app.timers.register(_timer, first_interval=0.1, persistent=True)
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    NS["jarvis_face_installed"] = True
    NS["jarvis_face_srv"] = srv
    print("RESULT: INSTALLED port=%d %dx%d@%.0ffps" % (PORT, W, H, FPS))

_install()
'''


def install(conn):
    """Send the frame-server CODE into Blender via a BlenderConnection."""
    return conn.send("execute_code", {"code": CODE})
```

- [ ] **Step 2: Install live and verify the timer/server start**

Send `blender_frame_server.CODE` via MCP `execute_blender_code`.
Expected printed output: `RESULT: INSTALLED port=8770 384x384@30fps` (or `ALREADY_INSTALLED` on re-run).

- [ ] **Step 3: Smoke-test the endpoint**

Run: `curl -s -o /tmp/face_probe.jpg -w '%{http_code} %{content_type}\n' http://127.0.0.1:8770/frame.jpg && file /tmp/face_probe.jpg`
Expected: `200 image/jpeg` and `JPEG image data, ... 384 x 384`.

If the GPU offscreen API errors (version differences in `draw_view3d` / `read_color`), iterate the recipe live against the running Blender until `/frame.jpg` returns a valid JPEG. If a VIEW_3D area is required and absent, ensure Blender has a 3D viewport open (the kiosk's Blender always will).

- [ ] **Step 4: Visual confirm**

Open `/tmp/face_probe.jpg` (Read tool) — confirm it shows the lit, framed face on dark background.

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/animators/blender_frame_server.py
git commit -m "feat(face): Blender offscreen MJPEG frame server on :8770"
```

---

## Task 5: Orchestrator wiring (`blender_face.py`) + launcher

Evolve the prototype to: install scene + frame server on startup, resolve ARKit indices by name, and drive jaw from real loudness via the gate.

**Files:**
- Modify: `src/voice-agent/animators/blender_face.py`
- Create: `bin/jarvis-face-animator`

- [ ] **Step 1: Add imports and index resolution**

In `src/voice-agent/animators/blender_face.py`, add near the existing imports (after `from pathlib import Path`):

```python
from animators import face_anim_core as fac
from animators import blender_scene_setup, blender_frame_server
from animators.loudness_monitor import LoudnessMonitor
```

Add this method to `BlenderConnection` (after `set_shape_keys`):

```python
    def resolve_shape_indices(self, names):
        """Return {name: index} for the given ARKit shape-key names by reading
        the live FaceCap_Head key_blocks (robust to ordering differences)."""
        code = (
            "import bpy, json\n"
            "o = bpy.data.objects.get('FaceCap_Head')\n"
            "kb = o.data.shape_keys.key_blocks if o and o.data.shape_keys else []\n"
            "m = {k.name: i for i, k in enumerate(kb)}\n"
            "print('INDICES:' + json.dumps(m))\n"
        )
        result = self.send("execute_code", {"code": code})
        # The MCP addon embeds printed stdout in its result payload; the
        # original verify relies on `str(result)` containing the print, so we
        # match against str(result) (its repr keeps inner JSON double-quotes
        # intact). The shape map is flat, so a non-greedy {...} is safe.
        text = str(result) if result is not None else ""
        match = re.search(r"INDICES:(\{.*?\})", text)
        if not match:
            return {n: fac.ARKIT_INDEX_HINTS.get(n) for n in names}
        full = json.loads(match.group(1))
        return {n: full.get(n, fac.ARKIT_INDEX_HINTS.get(n)) for n in names}
```

Change `set_shape_keys` to accept a `{index: value}` mapping (it already does) — no change needed there; the orchestrator maps names→indices before calling it.

- [ ] **Step 2: Replace the verification + add installs in `main()`**

In `main()`, replace the existing "Verify FaceCap_Head exists" block (the `result = blender.send(... "YES" if obj ...)` through the `sys.exit(1)`) with:

```python
    # Install / refresh the scene (idempotent) and the frame server.
    logger.info("Installing Blender scene + frame server...")
    scene_res = blender_scene_setup.install(blender)
    logger.info("Scene setup: %s", scene_res)
    blender_frame_server.install(blender)

    # Resolve ARKit shape-key indices by name (robust to ordering).
    names = ["jawOpen", "mouthClose", "mouthFunnel", "mouthPucker"]
    name_to_idx = blender.resolve_shape_indices(names)
    if name_to_idx.get("jawOpen") is None:
        logger.error("FaceCap_Head has no 'jawOpen' shape key. Import the "
                     "FaceCap model (uid=%s) and re-run.",
                     blender_scene_setup.FACECAP_UID)
        sys.exit(1)
    logger.info("Resolved shape indices: %s", name_to_idx)
```

- [ ] **Step 3: Start the loudness monitor and rewrite the loop**

After `tracker.start()` add:

```python
    loudness = LoudnessMonitor()
    loudness.start()
    gain = float(os.getenv("JARVIS_FACE_JAW_GAIN", "6.0"))
```

Replace the body of the `while True:` loop (the `if is_speaking: ... time.sleep(FRAME_INTERVAL)` block) with:

```python
            speaking = tracker.is_speaking()
            level = loudness.level()
            target = fac.target_jaw(speaking, level, gain=gain)
            current_jaw = fac.smooth_jaw(
                current_jaw, target,
                attack=JAW_SMOOTH_ATTACK, decay=JAW_SMOOTH_DECAY)

            shapes = fac.shape_values(current_jaw)  # {name: value}
            values = {name_to_idx[name]: val
                      for name, val in shapes.items()
                      if name_to_idx.get(name) is not None}

            now = time.monotonic()
            if now - last_send >= FRAME_INTERVAL:
                max_change = max(
                    (abs(values[k] - current_values.get(k, 0)) for k in values),
                    default=0.0)
                if max_change > 0.003:
                    blender.set_shape_keys(values)
                    current_values = values
                    last_send = now

            time.sleep(FRAME_INTERVAL)
```

In the `finally:` block, add `loudness.stop()` before `tracker.stop()`.

- [ ] **Step 4: Verify the suite still passes and the module imports**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_face_anim_core.py tests/test_loudness_monitor.py -v`
Expected: PASS.

Run: `cd src/voice-agent && .venv/bin/python -c "from animators import blender_face"`
Expected: no import error.

- [ ] **Step 5: End-to-end live check**

With Blender running (MCP addon on) and JARVIS active, run the animator:
`cd src/voice-agent && .venv/bin/python animators/blender_face.py` (or via the launcher below).
Speak to JARVIS. Expected: the Blender head's mouth opens/closes with JARVIS's voice loudness and returns to neutral on silence; `curl http://127.0.0.1:8770/frame.jpg` shows the mouth moving across calls.

- [ ] **Step 6: Create the launcher**

Create `bin/jarvis-face-animator`:

```bash
#!/usr/bin/env bash
# Launch the JARVIS -> Blender face animator in the voice-agent venv.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE/src/voice-agent"
exec .venv/bin/python animators/blender_face.py "$@"
```

Run: `chmod +x bin/jarvis-face-animator`

- [ ] **Step 7: Commit**

```bash
git add src/voice-agent/animators/blender_face.py bin/jarvis-face-animator
git commit -m "feat(face): drive jaw from real loudness; auto-install scene+frame server"
```

---

## Task 6: Kiosk FaceStream + ring fallback

**Files:**
- Create: `src/voice-agent/desktop-tauri/src/components/FaceStream.jsx`
- Modify: `src/voice-agent/desktop-tauri/src/components/KioskHUD.jsx`
- Modify: `src/voice-agent/desktop-tauri/src-tauri/tauri.conf.json`

- [ ] **Step 1: Extend the CSP**

In `src/voice-agent/desktop-tauri/src-tauri/tauri.conf.json`, find the `img-src` directive in the `csp` string:

```
img-src 'self' data: blob: asset:;
```

Change it to:

```
img-src 'self' data: blob: asset: http://127.0.0.1:8770;
```

(Edit only the `img-src` portion of the single `csp` string on line 36; leave every other directive unchanged.)

- [ ] **Step 2: Create the FaceStream component**

Create `src/voice-agent/desktop-tauri/src/components/FaceStream.jsx`:

```jsx
import React, { useEffect, useRef } from 'react'

// JARVIS's live Blender face as a native MJPEG <img>. The webview refreshes
// the bitmap itself — NO per-frame React state (the voice reactor sphere was
// removed for exactly that cost; see .claude/rules/desktop-tauri.md).
//
// Health: onError -> not healthy; first successful onLoad -> healthy; if no
// frame arrives within CONNECT_TIMEOUT_MS of mount, report unhealthy so the
// parent shows the ring. The parent keeps this mounted (hidden) during
// fallback so the stream can recover on its own.
const FACE_STREAM_URL = 'http://127.0.0.1:8770/stream.mjpg'
const CONNECT_TIMEOUT_MS = 2500

export function FaceStream({ size, onHealth }) {
  const imgRef = useRef(null)

  useEffect(() => {
    const img = imgRef.current
    if (img) img.src = `${FACE_STREAM_URL}?t=${Date.now()}`
    const timer = setTimeout(() => {
      const ok = img && img.complete && img.naturalWidth > 0
      onHealth(Boolean(ok))
    }, CONNECT_TIMEOUT_MS)
    return () => clearTimeout(timer)
  }, [onHealth])

  return (
    <img
      ref={imgRef}
      width={size}
      height={size}
      alt=""
      onError={() => onHealth(false)}
      onLoad={() => onHealth(true)}
      style={{ width: size, height: size, objectFit: 'cover', display: 'block',
               borderRadius: '50%' }}
    />
  )
}
```

- [ ] **Step 3: Wire FaceStream into KioskHUD with ring fallback**

In `src/voice-agent/desktop-tauri/src/components/KioskHUD.jsx`:

Add the import after line 5:

```jsx
import { FaceStream } from '@/components/FaceStream'
```

Add near the other module constants (after line 21 `const AURA_SIZE = 448`):

```jsx
// Face kiosk on by default; set VITE_JARVIS_FACE_KIOSK=0 to force ring-only.
const FACE_ENABLED = import.meta.env.VITE_JARVIS_FACE_KIOSK !== '0'
```

Add state inside `KioskHUD()` next to the other `useState` calls (after line 51):

```jsx
  const [faceOk, setFaceOk] = useState(false)
```

Replace the visualizer mount block (lines 157–173, the `<div style={{ position:'fixed', top:auraTop ... }}>` wrapping `<AgentAudioVisualizerAura .../>`) with:

```jsx
      <div
        style={{
          position: 'fixed',
          top: auraTop, left: auraLeft,
          width: AURA_SIZE, height: AURA_SIZE,
          zIndex: 9999,
        }}
      >
        {FACE_ENABLED && (
          <div style={{ display: faceOk ? 'block' : 'none',
                        width: AURA_SIZE, height: AURA_SIZE }}>
            <FaceStream size={AURA_SIZE} onHealth={setFaceOk} />
          </div>
        )}
        {(!FACE_ENABLED || !faceOk) && (
          <AgentAudioVisualizerAura
            size="xl"
            color="#1FD5F9"
            colorShift={0.05}
            state={agentState}
            themeMode="dark"
            audioTrack={agentTrack || undefined}
          />
        )}
      </div>
```

Update the diagnostic readout (line 204) to include face health — change:

```jsx
        {vp.w}×{vp.h} · {agentState} · {lkStatus}
```

to:

```jsx
        {vp.w}×{vp.h} · {agentState} · {lkStatus} · {FACE_ENABLED ? (faceOk ? 'face' : 'ring') : 'ring-only'}
```

- [ ] **Step 4: Build the front-end (catches syntax/import errors)**

Run: `cd src/voice-agent/desktop-tauri && npm run build`
Expected: build succeeds, no errors.

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/desktop-tauri/src/components/FaceStream.jsx \
        src/voice-agent/desktop-tauri/src/components/KioskHUD.jsx \
        src/voice-agent/desktop-tauri/src-tauri/tauri.conf.json
git commit -m "feat(kiosk): swap ring for live Blender FaceStream, ring fallback"
```

---

## Task 7: End-to-end verification + install

**Files:** none (verification only).

- [ ] **Step 1: Full voice-agent suite**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/ -q`
Expected: all pass (no regressions from the new modules).

- [ ] **Step 2: Bring up the pipeline**

1. Blender running with MCP addon (port 9876).
2. `bin/jarvis-face-animator` running (installs scene+server, starts driving).
3. `curl http://127.0.0.1:8770/frame.jpg` → `200 image/jpeg`.

- [ ] **Step 3: Kiosk visual + fallback test**

Rebuild & re-embed for the installed kiosk:
`cd src/voice-agent/desktop-tauri && npm run build && cargo build --release`
Launch the kiosk (`?route=kiosk`). Verify:
- The **face** appears in the ring's slot and its mouth tracks JARVIS's voice.
- Kill `bin/jarvis-face-animator` / stop the frame server → kiosk **falls back to the ring** within ~2.5 s (diagnostic shows `ring`).
- Restart the animator → face returns (diagnostic shows `face`).

- [ ] **Step 4: Final commit (if any tuning changed)**

```bash
git add -A && git commit -m "chore(face): end-to-end tuning for kiosk talking face"
```

---

## Self-review notes (author)

- **Spec coverage:** Component A → Task 3; Component B → Tasks 1,2,5; Component C (Blender) → Task 4; Component C (kiosk) → Task 6; failure/fallback → Tasks 4,6,7; testing → Tasks 1,2,7. All spec sections mapped.
- **R1 (index drift):** handled by `resolve_shape_indices` (name-based) in Task 5; hints in `face_anim_core`.
- **R4 (offscreen on main thread):** frame server uses `bpy.app.timers` (main thread) + a read-only HTTP thread; Task 4 Step 3 validates live.
- **No per-frame React state** (desktop rule): `<img>` MJPEG; `faceOk` flips only on health change.
- **Type consistency:** `shape_values` returns name-keyed dict everywhere; orchestrator maps names→indices once via `name_to_idx`; `set_shape_keys` consumes `{index: value}` as in the original file.
```
