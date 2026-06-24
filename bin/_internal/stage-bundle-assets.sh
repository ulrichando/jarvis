#!/usr/bin/env bash
# Stage the voice stack into the Tauri bundle-assets tree, mirroring the repo's
# `src/...` layout so the SAME run-manifest (repo-relative paths) resolves both
# in dev (against repo_root) and in an installed bundle (against the resource
# dir). Run before `tauri build` (wired as beforeBundleCommand). Phase 2 of the
# unified-app design (docs/superpowers/plans/2026-06-24-unified-app-phase2-bundle.md).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"              # bin/_internal/../.. = repo root
STAGE="$ROOT/src/desktop-tauri/src-tauri/bundle-assets"
VA="$ROOT/src/voice-agent"

rm -rf "$STAGE"
mkdir -p "$STAGE/src/voice-agent" "$STAGE/src/desktop-tauri/src-tauri/resources"

# voice-agent source + configs + the SFU binary (everything EXCEPT the venv,
# caches, and tests — none needed at runtime). ~64 MB.
rsync -a \
  --exclude '.venv/' --exclude '__pycache__/' --exclude 'tests/' --exclude '*.pyc' \
  "$VA/" "$STAGE/src/voice-agent/"

# The venv. `cp -a` preserves symlinks, so bin/python stays a link to
# /usr/bin/python3.13 (present on this machine; portability = Phase 3). ~3.6 GB.
cp -a "$VA/.venv" "$STAGE/src/voice-agent/.venv"

# The run-manifest (lives under src-tauri/resources, not voice-agent) — mirror
# its repo path so asset_root + the manifest's repo-relative paths line up.
cp "$ROOT/src/desktop-tauri/src-tauri/resources/run-manifest.json" \
   "$STAGE/src/desktop-tauri/src-tauri/resources/run-manifest.json"

echo "[stage] bundle-assets ready: $(du -sh "$STAGE" | cut -f1) at $STAGE"
