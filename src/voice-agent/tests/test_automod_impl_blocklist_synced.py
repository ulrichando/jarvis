"""Guard: bin/jarvis-automod-impl's fallback blocklist must list every
HARD_BLOCKLIST_PATHS entry, so layer-1 (the build prompt) can't silently drift
from layers 2/3 (test_gate.validate_diff)."""
from __future__ import annotations

import sys
from pathlib import Path

_VA_ROOT = Path(__file__).resolve().parent.parent
if str(_VA_ROOT) not in sys.path:
    sys.path.insert(0, str(_VA_ROOT))

_REPO_ROOT = _VA_ROOT.parent.parent  # tests/ -> voice-agent -> src -> repo root


def test_wrapper_fallback_lists_every_blocklist_path():
    from pipeline.automod._state import HARD_BLOCKLIST_PATHS

    wrapper = (_REPO_ROOT / "bin" / "jarvis-automod-impl").read_text(encoding="utf-8")
    missing = [p for p in HARD_BLOCKLIST_PATHS if f"- {p}" not in wrapper]
    assert not missing, f"wrapper fallback blocklist missing: {missing}"
