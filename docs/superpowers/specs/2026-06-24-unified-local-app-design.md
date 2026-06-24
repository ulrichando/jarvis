# Unified Local App — bundling the voice-agent into the desktop, one installable JARVIS

**Status:** design / approved-shape (2026-06-24)
**Goal:** Ship and run the voice-agent (the Python brain) and the desktop-tauri UI as **one installable, updatable application** on the user's main computer — one installer, one icon, one update — without rewriting or behaviourally changing the brain.

---

## 1. Context — why this, and what's true today

The user is standing up a VPS that will host **only the web app**. The voice-agent and the
desktop UI both run **only on the main computer** (the box with the mic, speakers, GPU, and
X11). They are already tightly coupled:

- `src/voice-agent/desktop-tauri/src-tauri/src/main.rs` already **spawns processes** and already
  **controls the voice-agent via `systemctl --user restart jarvis-voice-agent.service`**
  (36 spawn/systemctl references; `kiosk.rs` has 6 more). It already honours the
  "don't restart within 60 s of the last turn" rule in code.
- The brain runs as a separate systemd `--user` service:
  `ExecStart=.venv/bin/python jarvis_agent.py start` (WorkingDirectory `src/voice-agent`).
- Today's launch orchestration lives in `src/cli/scripts/start-desktop.sh` — it sources
  `.env` + `~/.jarvis/keys.env`, then launches the `jarvis-desktop` binary. **`src/cli/` is a
  separate codebase and is off-limits to edit** (CLAUDE.md). The design therefore moves launch
  responsibility *into the desktop*, leaving `src/cli/` untouched.

**The hard constraint:** the brain is Python (LiveKit Agents + ctranslate2 + onnxruntime +
ai_edge_litert + opencv + 2.1 GB of NVIDIA CUDA libs; **no torch**). The desktop is a compiled
Rust binary. You cannot compile the Python ML stack into the Rust binary. So "one codebase" can
only mean one of: (a) bundle the Python as a sidecar the desktop ships+runs, or (b) rewrite the
brain in Rust. (b) is a multi-month rewrite that discards 3,360 passing tests and the LiveKit
Agents framework — rejected as the direct opposite of "don't break anything."

**Decision: Option A — bundle, and do it by shipping the working venv, not by freezing it.**

### Why ship the venv instead of PyInstaller-freezing it

Freezing this *specific* stack is a documented failure mode: PyInstaller bundles of
ctranslate2 + onnxruntime + CUDA "build successfully but then cannot find the CUDA runtime
libraries at execution" — silent runtime breakage, the exact thing this project must avoid.
References:
- PyInstaller CUDA runtime-lib resolution: https://github.com/pyinstaller/pyinstaller/issues/7175
- PyInstaller ONNX Runtime bundling: https://github.com/pyinstaller/pyinstaller/issues/8083

Instead, the desktop bundles the **existing, working venv** as Tauri `resources` and spawns
`python jarvis_agent.py start` exactly as systemd does today. The runtime is **byte-identical**
to what runs now; the 2.1 GB of CUDA libs load the same way they already do. Tauri's sidecar /
resource bundling is first-class and has working Python precedents:
- Tauri v2 sidecar docs: https://v2.tauri.app/develop/sidecar/
- Tauri + Python sidecar example: https://github.com/dieharders/example-tauri-v2-python-server-sidecar

---

## 2. Architecture

### 2.1 The unified app

```
jarvis-desktop (Tauri app bundle)
├── Rust shell + React UI               (unchanged role: window, tray, IPC)
├── process supervisor  (NEW module)    spawns/health-checks/stops child procs
└── bundle resources (NEW):
    ├── voice-agent venv  (3.6 GB)       the brain, byte-identical to today
    ├── voice-agent source              jarvis_agent.py + the module tree
    ├── livekit-server.bin              the local SFU (already a standalone binary)
    └── run manifest                    ports, env, health endpoints, start order
externally managed (NOT in the bundle):
    ├── kokoro-tts  (docker container)   app checks/starts via docker compose
    └── ollama      (optional)           app checks if present
```

The desktop becomes the single thing the user launches. On launch its **supervisor** starts the
SFU, then the brain, waits for health, then reveals the UI. On quit it stops what it started.

### 2.2 Components & responsibilities

- **Process supervisor** (`src/voice-agent/desktop-tauri/src-tauri/src/supervisor.rs`, NEW) — owns the
  lifecycle of child processes: ordered start (SFU → brain), readiness/health polling, restart
  on crash (bounded), graceful shutdown, and the existing "don't restart within 60 s of last
  turn" guard (lifted from `main.rs`). One clear responsibility: child-process lifecycle.
- **Run manifest** (`src/voice-agent/desktop-tauri/src-tauri/resources/run-manifest.json`, NEW) — declares
  each managed process: command, args, working dir, env file, health URL, start order, restart
  policy. Keeps the supervisor data-driven instead of hard-coding paths.
- **Brain** — unchanged. Invoked as `<resource>/venv/bin/python <resource>/voice-agent/jarvis_agent.py start`.
- **SFU** — `livekit-server.bin --config <bundled config>`; bundled as a resource (it's already a
  self-contained binary, no freezing).
- **Config/keys** — the supervisor sources `~/.jarvis/keys.env` + repo `.env` the same way
  `start-desktop.sh` does today (logic moved into the desktop, so `src/cli/` is not edited).

### 2.3 Data flow (unchanged at the wire level)

The desktop ↔ brain interfaces are exactly today's: HTTP on the voice-client port + the bridge
on `127.0.0.1:8765`, the brain ↔ SFU on `ws://127.0.0.1:7880`, TTS → kokoro on `:8880`. Nothing
about how they *talk* changes — only how they're *started and shipped*.

---

## 3. External services (the careful wrinkles)

- **CUDA** — the bundled venv carries the userspace CUDA libs (the 2.1 GB `nvidia/`); only the
  host **kernel driver** must already be installed (it is). First-run check verifies CUDA and
  falls back per the existing config (`JARVIS_STT_LOCAL_ONLY`, etc.). No drivers are bundled.
- **Kokoro TTS** — stays a docker container (`kokoro-tts`). The supervisor checks `:8880` and
  runs `docker compose up -d` for it if down; if docker is absent it surfaces the existing
  Orpheus fallback path. Not bundled into the app (it's a container, not Python).
- **LiveKit SFU** — bundled binary, started first (the brain needs it).
- **Ollama** — optional; checked at `:11434`, never bundled.

---

## 4. Phasing — reversible, nothing-breaks order

Each phase ships and reverts independently. The working systemd path is never removed until its
replacement is proven.

- **Phase 0 — spawn spike (no bundling, no removal).** Add the supervisor + manifest; have the
  desktop spawn the brain **pointing at the existing in-tree venv** and the SFU, poll health, and
  pass a smoke turn. Proves desktop-owned lifecycle works. systemd untouched. *On the single main
  computer this is nearly free — the venv is already there.*
- **Phase 1 — desktop owns lifecycle, systemd in parallel.** A flag
  (`JARVIS_DESKTOP_OWNS_AGENT`) selects desktop-spawn vs the legacy systemd service. Prove parity
  (same behaviour, same telemetry) over normal use.
- **Phase 2 — bundle into the app, flip the default.** Copy the venv + source + SFU into Tauri
  `resources`/`externalBin`; the installed app runs entirely from its bundle. Default flips to
  desktop-owned; systemd demoted to optional/legacy.
- **Phase 3 — portability + delta updates (OPTIONAL, only if distributing to other machines).**
  Replace the in-place venv with a relocatable `python-build-standalone` runtime and add a delta
  update strategy so a 4 GB app doesn't ship 4 GB per update. Not needed for a one-computer setup.

---

## 5. Don't-break-anything strategy

1. **The brain's source never changes** — all 3,360 voice-agent tests pass unchanged at every
   phase because the Python didn't move.
2. **The runtime is byte-identical** — we ship the same venv, not a frozen re-pack, so CUDA/native
   lib loading is exactly today's.
3. **systemd kept in parallel** behind a flag until Phase 2 proves the bundled path.
4. **Reversible phases** — any phase can be turned off by a flag/revert without a broken assistant.
5. **`src/cli/` untouched** — launch logic moves *into* the desktop rather than editing the
   off-limits `start-desktop.sh`.

---

## 6. Error handling & supervision

- **Ordered start with health gates** — SFU up (port listen) → brain up (health endpoint) → UI
  revealed. A failed dependency surfaces a clear UI error, not a silent hang.
- **Bounded restart** — on child crash, restart up to N times within a window, then stop and
  surface the failure (mirrors the watchdog philosophy; no infinite respawn).
- **Restart-safety guard preserved** — the 60-s-since-last-turn rule moves into the supervisor.
- **Shutdown** — desktop quit stops children it started, in reverse order; children it did *not*
  start (e.g. a pre-existing systemd service in Phase 1) are left alone.

---

## 7. Testing & verification

- **Unchanged:** `cd src/voice-agent && .venv/bin/python -m pytest tests/` (3,360 tests) — must
  stay green at every phase (proves the brain is untouched).
- **NEW — packaging smoke test:** the bundled/pointed-at venv boots `jarvis_agent.py start` and
  completes one smoke turn (reuse `pipeline/automod/selftest.py`'s smoke-turn).
- **NEW — supervisor lifecycle test:** desktop spawns → health-passes → restarts cleanly on
  simulated crash → stops on quit; the 60-s guard fires.
- **NEW — parity check (gates Phase 2):** desktop-owned vs systemd produce identical turn
  behaviour + telemetry over a fixed script.
- **Desktop build:** `npm run build && cargo build --release` (both — re-embeds dist; CLAUDE.md).

---

## 8. The size & update caveat (accepted)

The app is **~4 GB** (3.6 GB venv, 2.1 GB CUDA). This weight already lives on disk today;
bundling makes it the app's size. Updating a 4 GB app naively means 4 GB downloads — acceptable
on a single self-managed machine (you rebuild locally), but if Phase 3 ever happens, delta
updates (binary-diff or splitting the rarely-changing CUDA/venv layer from the fast-changing
app code) are required. The brain's *code* is tiny; the *weight* is CUDA + models.

---

## 9. Scope (per `.claude/rules/regression-prevention.md`)

```
SCOPE:  src/voice-agent/desktop-tauri/src-tauri/src/   (NEW supervisor.rs; main.rs/kiosk.rs spawn refactor)
        src/voice-agent/desktop-tauri/src-tauri/tauri.conf.json   (+externalBin, +resources)
        src/voice-agent/desktop-tauri/src-tauri/resources/         (NEW run-manifest + bundled assets)
        setup/systemd/                                  (kept parallel; demoted in Phase 2)
        scripts/ or bin/                                (NEW packaging/build helper)
        docs/superpowers/specs/, docs/superpowers/plans/
OUT:    src/voice-agent/ source        (runtime UNCHANGED — the load-bearing safety premise)
        src/cli/                        (separate codebase, off-limits; launch logic moves INTO
                                         the desktop, start-desktop.sh is NOT edited)
        src/web/, src/android/          (untouched)
        src/voice-agent/pipeline/automod/ (evolution tree; unrelated)
WHY OUT: the entire safety guarantee is that the brain's behaviour/code does not change; src/cli
         is a separate product per CLAUDE.md.
```

---

## 10. Open questions / deferred

- **Installer format** — `.deb` / AppImage / both? (Tauri targets currently default; pick in the
  plan.) Affects how the 4 GB resources are delivered.
- **Where the bundled venv lives at runtime** — inside the app bundle (read-only) vs a first-run
  copy to `~/.jarvis/runtime/` (writable, survives app replacement). Lean: read-only in-bundle for
  Phase 2; revisit if the brain needs to write into its own tree.
- **Phase 3 is explicitly optional** — only pursued if the user distributes beyond the main box.
