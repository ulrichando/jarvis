"""Guard: the supervisor's registry tool surface must have NO duplicate
function names.

Why this exists as its own test: LiveKit's ``llm.ToolContext(tools).flatten()``
raises ``ValueError: duplicate function name: X`` at **session start** — not
during tool registration and not in the rest of the unit suite. So a duplicate
tool name passes every other test green and only crashes the agent the moment a
voice client connects. Regression guard for the 2026-05-21 "duplicate
web_search" incident (an inline tool + a registry tool shared a name before the
registry-only rewrite removed the inline ones from the agent's tool list).
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_loaded_tools_have_no_duplicate_names():
    from tools._adapter import load_all_livekit_tools

    names = [t.info.name for t in load_all_livekit_tools()]
    dups = sorted(n for n, c in Counter(names).items() if c > 1)
    assert not dups, (
        "duplicate tool names would crash LiveKit session start "
        f"(ToolContext.flatten): {dups}"
    )
