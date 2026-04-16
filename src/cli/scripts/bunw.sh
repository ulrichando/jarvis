#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

normalize_os() {
  case "$(uname -s)" in
    Linux)
      printf 'linux\n'
      ;;
    Darwin)
      printf 'darwin\n'
      ;;
    MINGW*|MSYS*|CYGWIN*)
      printf 'windows\n'
      ;;
    *)
      return 1
      ;;
  esac
}

normalize_arch() {
  case "$(uname -m)" in
    x86_64|amd64)
      printf 'x64\n'
      ;;
    arm64|aarch64)
      printf 'arm64\n'
      ;;
    *)
      return 1
      ;;
  esac
}

resolve_bun() {
  local candidate
  local os
  local arch
  local platform_dir=""
  local binary_name="bun"

  if os="$(normalize_os)" && arch="$(normalize_arch)"; then
    if [ "${os}" = "windows" ]; then
      binary_name="bun.exe"
    fi
    platform_dir="${ROOT}/vendor/bun/${os}-${arch}/${binary_name}"
  fi

  if [ -n "${BUN_BIN:-}" ] && [ -x "${BUN_BIN}" ]; then
    printf '%s\n' "${BUN_BIN}"
    return 0
  fi

  if [ -n "${platform_dir}" ] && [ -x "${platform_dir}" ]; then
    printf '%s\n' "${platform_dir}"
    return 0
  fi

  for candidate in \
    "${ROOT}/vendor/bun/bin/${binary_name}" \
    "${ROOT}/tools/bun/bin/${binary_name}" \
    "${ROOT}/.bun/bin/${binary_name}"
  do
    if [ -x "${candidate}" ]; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done

  if command -v bun >/dev/null 2>&1; then
    command -v bun
    return 0
  fi

  for candidate in \
    "${HOME:-}/.bun/bin/${binary_name}" \
    "/usr/local/bin/bun" \
    "/opt/homebrew/bin/bun" \
    "/usr/bin/bun"
  do
    if [ -x "${candidate}" ]; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done

  return 1
}

if BUN_PATH="$(resolve_bun)"; then
  exec "${BUN_PATH}" "$@"
fi

echo "Error: Bun was not found." >&2
echo "Provide it via one of:" >&2
echo "  1. BUN_BIN=/absolute/path/to/bun" >&2
echo "  2. PATH containing bun" >&2
echo "  3. A repo-bundled binary at vendor/bun/<os>-<arch>/bun or vendor/bun/bin/bun" >&2
exit 1
