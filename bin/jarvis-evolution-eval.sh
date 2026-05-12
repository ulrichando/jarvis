#!/usr/bin/env bash
# Nightly golden eval — runs at 06:00 local via systemd timer or cron.
# Writes a report to ~/.jarvis/evolution_golden_report.<date>.json.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT/src/voice-agent"

DATE_TAG="$(date -u +%Y-%m-%d)"
OUT="$HOME/.jarvis/evolution_golden_report.$DATE_TAG.json"

mkdir -p "$HOME/.jarvis"
.venv/bin/python -c "
import json
from pathlib import Path
from pipeline.evolution.store import RuleStore
from pipeline.evolution import golden_eval

store = RuleStore()
loaded = store.load()
report = golden_eval.run(
    rules=loaded.anchor + loaded.core + loaded.accepted + loaded.staged
)
Path('$OUT').write_text(json.dumps(report, indent=2))
print(f'wrote {len(report[\"misses\"])} miss(es) to $OUT')
"
