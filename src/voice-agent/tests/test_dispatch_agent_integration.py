"""Integration tests for dispatch_agent — spawns real bin/jarvis.

Skipped when ANTHROPIC_API_KEY is missing (no LLM ↔ no useful subagent run).
Skipped when bin/jarvis doesn't exist (e.g., fresh checkout pre-setup).

Each test that actually invokes bin/jarvis costs real API tokens. Keep the
prompts tiny.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

_BIN_JARVIS = Path(__file__).resolve().parents[3] / "bin" / "jarvis"
_HAS_KEY = bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())


@pytest.mark.skipif(not _BIN_JARVIS.exists(), reason="bin/jarvis missing")
@pytest.mark.skipif(not _HAS_KEY, reason="ANTHROPIC_API_KEY unset")
@pytest.mark.asyncio
async def test_real_dispatch_explore_finds_file_path():
    """End-to-end Explore dispatch — should return a string mentioning the file."""
    from tools.dispatch_agent import handle_dispatch_agent

    # 30s timeout default for explore; this prompt should easily fit.
    result = await handle_dispatch_agent({
        "subagent_type": "explore",
        "task": (
            "find the path of the file that defines the dispatch_agent tool. "
            "Reply with only the path, nothing else."
        ),
        "description": "find dispatch_agent file",
    })

    # The result is either the subagent's stdout or a JSON error object.
    # On success, the answer must mention 'dispatch_agent.py' somewhere.
    assert "dispatch_agent.py" in result, (
        f"expected the subagent to find dispatch_agent.py; got: {result!r}"
    )
