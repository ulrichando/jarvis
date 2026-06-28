#!/usr/bin/env bash
# Build a self-contained `jarvis` binary via `bun build --compile`.
#
# Why this exists: the CLI is normally source-run (bin/jarvis → start.sh →
# `bun --feature=… cli.tsx`). A distributable binary must bake the SAME
# --feature flags in at compile time — without them every feature-gated tool
# is dead-code-eliminated (this is exactly why an earlier feature-less compile
# shipped a binary with zero working tools).
#
# The optional cloud-provider SDKs (AWS Bedrock / Azure / Anthropic Foundry /
# Smithy) are lazy `await import()`-ed in source and never installed, so a plain
# --compile fails to resolve them, and marking them --external makes the binary
# crash at startup on a missing module. Fix: install them transiently
# (--no-save, so package.json/lockfile stay clean) so they BUNDLE into the
# binary. jarvis routes through the proxy and never executes these paths, but
# the bytes must be present for the binary to load. `modifiers-napi` is a darwin
# -only native addon (Linux returns before the require) — kept external.
#
# Usage:  bash scripts/build-binary.sh [outfile]
#   outfile default: dist/jarvis-<os>-<arch>
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$SCRIPT_DIR/.."
cd "$ROOT"

OS="$(uname -s | tr '[:upper:]' '[:lower:]')"   # linux / darwin
ARCH="$(uname -m)"                               # x86_64 / aarch64
case "$ARCH" in x86_64) ARCH=x64 ;; aarch64|arm64) ARCH=arm64 ;; esac
OUTFILE="${1:-$ROOT/dist/jarvis-$OS-$ARCH}"
mkdir -p "$(dirname "$OUTFILE")"

# Feature flags — keep in lockstep with scripts/start.sh's CLI_CMD. Sourced from
# there so the two never drift: a tool enabled for source-run but missing from
# the binary (or vice-versa) is a silent capability gap.
mapfile -t FEATURES < <(grep -oP '(?<=--feature=)\S+' "$SCRIPT_DIR/start.sh")
FEATURE_ARGS=()
for f in "${FEATURES[@]}"; do FEATURE_ARGS+=("--feature=$f"); done
echo "[build] ${#FEATURES[@]} features: ${FEATURES[*]}"

# Cloud SDKs to bundle (transient install — no package.json change).
CLOUD_DEPS=(
  @aws-sdk/client-bedrock
  @aws-sdk/client-bedrock-runtime
  @aws-sdk/client-sts
  @aws-sdk/credential-providers
  @azure/identity
  @anthropic-ai/mcpb
  @anthropic-ai/foundry-sdk
  @smithy/node-http-handler
  @smithy/fetch-http-handler
)
echo "[build] installing ${#CLOUD_DEPS[@]} optional cloud SDKs (--no-save)…"
bun add --no-save "${CLOUD_DEPS[@]}" >/dev/null 2>&1 || {
  echo "[build] WARN: optional SDK install failed; binary may crash on a missing module" >&2
}

# MACRO defines — match start.sh. VERSION is read from start.sh's MACRO.VERSION
# (not package.json) so the binary reports the SAME version the source-run path
# does — the two must agree or `jarvis --version` lies depending on install kind.
VERSION="$(grep -oP "MACRO\.VERSION=\"\K[^\"]+" "$SCRIPT_DIR/start.sh" | head -1)"
VERSION="${VERSION:-0.0.0}"
echo "[build] version $VERSION → $OUTFILE"

bun build --compile \
  "${FEATURE_ARGS[@]}" \
  --external 'modifiers-napi' \
  --define "MACRO.VERSION=\"$VERSION\"" \
  --define 'MACRO.BUILD_TIME=""' \
  --define 'MACRO.PACKAGE_URL="@anthropic-ai/claude-code"' \
  --define 'MACRO.NATIVE_PACKAGE_URL="@anthropic-ai/claude-code-native"' \
  --define 'MACRO.ISSUES_EXPLAINER="report the issue at https://github.com/ulrichando/jarvis/issues"' \
  --define 'MACRO.FEEDBACK_CHANNEL="https://github.com/ulrichando/jarvis/issues"' \
  --define 'MACRO.VERSION_CHANGELOG=null' \
  src/entrypoints/cli.tsx \
  --outfile "$OUTFILE"

chmod +x "$OUTFILE"
SIZE="$(du -h "$OUTFILE" | cut -f1)"
echo "[build] done: $OUTFILE ($SIZE)"
echo "[build] smoke test: $OUTFILE --version"
"$OUTFILE" --version || { echo "[build] FAIL: binary did not run" >&2; exit 1; }
