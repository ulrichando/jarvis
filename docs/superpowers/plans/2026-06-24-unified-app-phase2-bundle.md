# Unified Local App — Phase 2: bundle the venv + SFU into an installable app

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. Steps use `- [ ]`.

**Goal:** Turn the desktop from a bare binary run out of the dev tree into an **installable bundled app** that carries the voice-agent (venv + source), the SFU binary, and the run-manifest as Tauri resources, and runs entirely from its install location.

**Architecture:** Add a resource-dir-aware `asset_root` resolution so the supervisor finds the stack under `BaseDirectory::Resource` when installed (falling back to `repo_root()` in dev). Stage the assets — **mirroring the repo's `src/voice-agent/` layout** so the *same* run-manifest works in both dev and bundle. Enable `bundle.active` + `bundle.resources`. Build with `cargo tauri build`.

**Prereqs established (2026-06-24):**
- Venv is relocatable on the same machine: `.venv/bin/python` → symlink `/usr/bin/python3.13`, no absolute paths in `.pth`, brain launched via direct `python` (not console scripts, whose shebangs are absolute). Target needs `/usr/bin/python3.13` (true here; other machines = Phase 3).
- Tauri v2: `bundle.resources` map form `"src/path": "dest/path"` includes dir trees; runtime `app.path().resolve("x", BaseDirectory::Resource)`.
- Phase 0/1 supervisor committed (`5c162aa6`), default OFF; `bundle.active` currently `false`.

**Scope:**
```
SCOPE: src/desktop-tauri/src-tauri/src/main.rs   (asset_root resolution in the setup hook)
       src/desktop-tauri/src-tauri/tauri.conf.json (bundle.active+targets+resources)
       bin/_internal/stage-bundle-assets.sh        (NEW staging script)
OUT:   supervisor.rs core logic (unchanged — it already takes a root path),
       src/voice-agent/** source (unchanged), src/cli/**, the folder-fusion (task #5)
WHY OUT: Phase 2 is packaging; the brain + supervisor logic don't change.
```

---

### Task 1: `asset_root` resolution (resource-dir-aware), default still OFF

**Files:** Modify `src/desktop-tauri/src-tauri/src/main.rs` (the Phase 0/1 setup hook).

- [ ] **Step 1:** In the setup hook, replace the hard `repo_root()` manifest resolution with resource-dir-aware logic. Add `use tauri::path::BaseDirectory;` (or fully-qualify). New block:

```rust
{
    // Bundled install: assets are staged under the Tauri resource dir, mirroring
    // the repo's `src/...` layout, so the SAME run-manifest resolves in both
    // contexts. Dev: the repo tree. Prefer whichever actually has the manifest.
    const MANIFEST_REL: &str = "src/desktop-tauri/src-tauri/resources/run-manifest.json";
    let (root, manifest) = match app
        .path()
        .resolve(MANIFEST_REL, tauri::path::BaseDirectory::Resource)
    {
        Ok(m) if m.exists() => {
            let res_root = app
                .path()
                .resolve(".", tauri::path::BaseDirectory::Resource)
                .unwrap_or_else(|_| repo_root());
            (res_root, m)
        }
        _ => {
            let r = repo_root();
            let m = r.join(MANIFEST_REL);
            (r, m)
        }
    };
    let mut env_files = _repo_env_files();
    env_files.push(_keys_file()); // keys.env (~/.jarvis, user-local) — present even when installed
    let sup = supervisor::maybe_start_managed_stack(&root, &manifest, &env_files);
    app.manage(Mutex::new(sup));
}
```

- [ ] **Step 2:** `cd src/desktop-tauri/src-tauri && cargo build` — expect clean compile (the dev path: `resolve(...Resource)` returns a non-existent path → falls back to `repo_root()`, identical to today).
- [ ] **Step 3:** `cargo test` — all existing tests still green (supervisor unit tests unaffected; this is caller-only).
- [ ] **Step 4:** Commit: `git add src/desktop-tauri/src-tauri/src/main.rs && git commit -m "feat(desktop): resource-dir-aware asset_root for bundled installs"`

---

### Task 2: staging script (mirror the src/voice-agent layout)

**Files:** Create `bin/_internal/stage-bundle-assets.sh`.

- [ ] **Step 1:** Write the script. It stages, into `src/desktop-tauri/src-tauri/bundle-assets/` (gitignored), a tree mirroring the repo so the manifest's `./src/voice-agent/...` paths resolve:

```bash
#!/usr/bin/env bash
# Stage the voice stack into the Tauri bundle-assets tree (mirrors repo layout).
# Run before `cargo tauri build`. cp -a preserves the venv's symlinks (bin/python
# → /usr/bin/python3.13) so the copied venv still finds the system interpreter.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
STAGE="$ROOT/src/desktop-tauri/src-tauri/bundle-assets"
VA="$ROOT/src/voice-agent"
rm -rf "$STAGE"
mkdir -p "$STAGE/src/voice-agent" "$STAGE/src/desktop-tauri/src-tauri/resources"
# venv (symlinks preserved), SFU binary + config, run-manifest.
cp -a "$VA/.venv"                "$STAGE/src/voice-agent/.venv"
cp -a "$VA/livekit-server.bin"   "$STAGE/src/voice-agent/livekit-server.bin"
cp -a "$VA/livekit.yaml"         "$STAGE/src/voice-agent/livekit.yaml"
cp "$ROOT/src/desktop-tauri/src-tauri/resources/run-manifest.json" \
   "$STAGE/src/desktop-tauri/src-tauri/resources/run-manifest.json"
# voice-agent python source (exclude the venv [copied above], caches, tests).
rsync -a --exclude '.venv' --exclude '__pycache__' --exclude 'tests' \
      --exclude '*.pyc' --include '*/' --include '*.py' --include '*.md' \
      --include '*.yaml' --include 'acp_registry/***' --exclude '*' \
      "$VA/" "$STAGE/src/voice-agent/"
echo "[stage] bundle-assets ready: $(du -sh "$STAGE" | cut -f1)"
```

- [ ] **Step 2:** Add `src/desktop-tauri/src-tauri/bundle-assets/` to `.gitignore` (it's a build artifact, ~3.7 GB — never commit). Verify it's ignored: `git check-ignore src/desktop-tauri/src-tauri/bundle-assets/`.
- [ ] **Step 3:** Run it: `bash bin/_internal/stage-bundle-assets.sh` — expect "bundle-assets ready: ~3.7G", and confirm `bundle-assets/src/voice-agent/.venv/bin/python` is still a symlink to `/usr/bin/python3.13`.
- [ ] **Step 4:** Commit the script + gitignore: `git add bin/_internal/stage-bundle-assets.sh .gitignore && git commit -m "feat(desktop): bundle-asset staging script for the voice stack"`

---

### Task 3: enable bundling + point resources at the staged tree

**Files:** Modify `src/desktop-tauri/src-tauri/tauri.conf.json`.

- [ ] **Step 1:** Flip `bundle.active` to `true`, keep `targets`, and add `resources` (map form) staging the mirrored tree into the bundle root:

```json
  "bundle": {
    "active": true,
    "targets": ["deb", "appimage"],
    "icon": ["icons/32x32.png", "icons/128x128.png"],
    "resources": {
      "bundle-assets/": ""
    }
  }
```

(The `"bundle-assets/": ""` map copies the staged tree to the resource-dir root, so `<resource>/src/voice-agent/...` + `<resource>/src/desktop-tauri/src-tauri/resources/run-manifest.json` exist — matching `asset_root` + the manifest's repo-relative paths.)

- [ ] **Step 2:** Add a `beforeBundleCommand` so staging always runs before a bundle:

```json
  "build": {
    "frontendDist": "../dist",
    "beforeBuildCommand": "npm run build",
    "beforeBundleCommand": "bash ../../../bin/_internal/stage-bundle-assets.sh"
  }
```

- [ ] **Step 3:** `cargo build` (no bundle) still compiles. Commit: `git add src/desktop-tauri/src-tauri/tauri.conf.json && git commit -m "feat(desktop): enable bundling with the staged voice stack as resources"`

---

### Task 4: build the installer + verify (the long pole)

- [ ] **Step 1:** `cd src/desktop-tauri && npm run build && npx tauri build` — **HEAVY**: LTO release + copying ~3.7 GB of resources → a ~4 GB `.deb`/AppImage. Expect 15-40 min. Run in background; watch for "Finished" + the artifact path under `src-tauri/target/release/bundle/`.
- [ ] **Step 2:** Install/extract the artifact to a scratch location and verify the layout: `<install>/.../resources/src/voice-agent/.venv/bin/python` exists, the manifest is at the mirrored path.
- [ ] **Step 3:** Live-prove (USER-run, like Phase 0/1, since it spawns the stack): launch the *installed* binary with `JARVIS_DESKTOP_OWNS_AGENT=1`, confirm `asset_root` resolves to the resource dir and the supervisor spawns the bundled SFU + brain (check `<resource>/.../.venv/bin/python` is the running brain's exe). Stop it; systemd unaffected.
- [ ] **Step 4 (flip default — separate decision):** Decide how the *installed* app enables ownership by default while the dev binary stays OFF. Lean: when `asset_root == resource_dir` (i.e., bundled), default the flag ON; dev stays OFF. Implement as a follow-up once Step 3 proves the spawn.

---

## Notes
- **Don't commit `bundle-assets/`** (3.7 GB build artifact) — gitignored in Task 2.
- The ~4 GB installer is inherent (2.1 GB CUDA + models); that weight already exists on disk.
- Portability to machines without `/usr/bin/python3.13` = Phase 3 (portable python). Phase 2 targets this machine.
- Verification per task: `cargo build`/`cargo test` (Tasks 1-3); the full bundle build is Task 4 only.
