#!/usr/bin/env bash
# Verify the two copies of client-core.ts are byte-identical.
#
# Why two copies: Next.js Turbopack refuses to import code outside
# `src/web/`. We can't symlink (Turbopack rejected it as "Invalid
# symlink") and a workspace package needs a build step we don't want
# to maintain. So the SDK core lives in two places, kept in sync by
# this script.
#
# Run:
#   bash scripts/check-hub-core-sync.sh           # check (exit 1 on drift)
#   bash scripts/check-hub-core-sync.sh --fix     # copy hub → web
#
# CI / pre-commit / pytest all run the check form. The fix form is
# manual when you've intentionally edited one side and want to
# propagate.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HUB="$ROOT/src/hub/client-core.ts"
WEB="$ROOT/src/web/src/lib/hub/client-core.ts"

if [[ ! -f "$HUB" ]]; then
    echo "[check-hub-core-sync] missing $HUB" >&2
    exit 2
fi
if [[ ! -f "$WEB" ]]; then
    echo "[check-hub-core-sync] missing $WEB" >&2
    exit 2
fi

if [[ "${1:-}" == "--fix" ]]; then
    cp "$HUB" "$WEB"
    echo "[check-hub-core-sync] copied $HUB → $WEB"
    exit 0
fi

if ! diff -q "$HUB" "$WEB" > /dev/null; then
    echo "[check-hub-core-sync] DRIFT detected:" >&2
    diff -u "$HUB" "$WEB" >&2 || true
    echo >&2
    echo "Fix: bash scripts/check-hub-core-sync.sh --fix" >&2
    exit 1
fi

echo "[check-hub-core-sync] hub/client-core.ts and web/.../client-core.ts are in sync"
