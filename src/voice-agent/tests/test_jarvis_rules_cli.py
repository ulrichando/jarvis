"""Smoke tests for bin/jarvis-rules sub-commands.

Invokes the script in a subprocess against a tmp_path store so the
real ~/.jarvis is untouched.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path


ANCHOR = """\
---
schema_version: 2
---

## ═══ ANCHOR ═══

- <!-- id=A-0001 tier=anchor --> Reply Yes?.
"""

REPO_ROOT = Path(__file__).resolve().parents[3]
CLI_PATH = REPO_ROOT / "bin" / "jarvis-rules"


def _make_store(tmp_path):
    anchor = tmp_path / "anchor.md"
    learned = tmp_path / "learned.md"
    anchor.write_text(ANCHOR)
    sha = hashlib.sha256(ANCHOR.encode()).hexdigest()
    learned.write_text(
        f"---\nschema_version: 2\nanchor_baseline_sha256: {sha}\n---\n\n"
        "## ═══ ACCEPTED ═══\n\n"
        '- <!-- id=R-0001 tier=accepted --> use --profile-directory=Default.\n'
        "## ═══ STAGED ═══\n\n"
        '- <!-- id=R-0002 tier=staged --> [STAGED] don\'t open chromium.\n'
    )
    return anchor, learned


def _run(tmp_path, *args):
    anchor, learned = _make_store(tmp_path)
    env = os.environ.copy()
    env["JARVIS_RULES_ANCHOR_PATH"] = str(anchor)
    env["JARVIS_RULES_LEARNED_PATH"] = str(learned)
    return subprocess.run(
        [sys.executable, str(CLI_PATH), *args],
        env=env, capture_output=True, text=True, timeout=10,
    )


def test_list_shows_all_tiers(tmp_path):
    proc = _run(tmp_path, "list")
    assert proc.returncode == 0
    assert "R-0001" in proc.stdout
    assert "R-0002" in proc.stdout


def test_diff_prints_rule_metadata(tmp_path):
    proc = _run(tmp_path, "diff", "R-0001")
    assert proc.returncode == 0
    assert "R-0001" in proc.stdout
    assert "tier" in proc.stdout.lower()
