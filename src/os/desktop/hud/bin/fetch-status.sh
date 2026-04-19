#!/usr/bin/env bash
# fetch-status.sh — fetch misty-core status and pending confirmations.
# Called by eww's defpoll; emits a single line of JSON on stdout per call.
# Shape: {"health":"ok"|"down","pending":[{...},{...}]}

set -euo pipefail

readonly BASE="${MISTY_URL:-http://127.0.0.1:8765}"

health=$(curl -sS --max-time 1 "$BASE/health" 2>/dev/null || true)
if [[ -z "$health" ]]; then
  echo '{"health":"down","pending":[]}'
  exit 0
fi

pending=$(curl -sS --max-time 2 "$BASE/api/confirmation" 2>/dev/null | jq -c '.pending // []' 2>/dev/null || echo '[]')
jq -cn --argjson pending "$pending" '{health:"ok", pending:$pending}'
