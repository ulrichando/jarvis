#!/usr/bin/env bash
# confirm.sh — POST a decision to misty-core's confirmation endpoint.
# Usage: confirm.sh <id> <allow|deny>

set -euo pipefail

readonly BASE="${MISTY_URL:-http://127.0.0.1:8765}"
readonly ID="${1:?usage: confirm.sh <id> <allow|deny>}"
readonly DECISION="${2:?usage: confirm.sh <id> <allow|deny>}"

if [[ "$DECISION" != "allow" && "$DECISION" != "deny" ]]; then
  echo "decision must be 'allow' or 'deny'" >&2
  exit 2
fi

curl -sS -X POST \
  -H 'content-type: application/json' \
  -d "{\"decision\":\"$DECISION\"}" \
  --max-time 3 \
  "$BASE/api/confirmation/$ID" >/dev/null
