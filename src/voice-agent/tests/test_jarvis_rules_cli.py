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


def test_refresh_anchor_baseline_updates_sha_only(tmp_path):
    """When the anchor file is legitimately edited, the operator needs a
    way to refresh the recorded sha256 in learned_rules.md WITHOUT
    re-running the full migrator (which would also touch rules)."""
    import hashlib

    anchor, learned = _make_store(tmp_path)

    # Bump the anchor file (simulating a legitimate `git commit` edit).
    anchor.write_text(anchor.read_text() + "- <!-- id=A-9999 tier=anchor --> new invariant.\n")
    new_sha = hashlib.sha256(anchor.read_text().encode()).hexdigest()

    # Pre-condition: the learned file's baseline does NOT match new_sha yet.
    assert new_sha not in learned.read_text()
    # And the rule R-0001 is still there (we want to preserve it).
    assert "R-0001" in learned.read_text()

    env = os.environ.copy()
    env["JARVIS_RULES_ANCHOR_PATH"] = str(anchor)
    env["JARVIS_RULES_LEARNED_PATH"] = str(learned)
    proc = subprocess.run(
        [sys.executable, str(CLI_PATH), "refresh-anchor-baseline"],
        env=env, capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode == 0, proc.stderr
    assert new_sha[:12] in proc.stdout  # operator-friendly preview

    # Post-condition: learned now has the new sha AND the rule survived.
    learned_text = learned.read_text()
    assert new_sha in learned_text
    assert "R-0001" in learned_text


def test_refresh_anchor_baseline_is_idempotent(tmp_path):
    """Running twice produces no change."""
    anchor, learned = _make_store(tmp_path)

    env = os.environ.copy()
    env["JARVIS_RULES_ANCHOR_PATH"] = str(anchor)
    env["JARVIS_RULES_LEARNED_PATH"] = str(learned)

    proc1 = subprocess.run(
        [sys.executable, str(CLI_PATH), "refresh-anchor-baseline"],
        env=env, capture_output=True, text=True, timeout=10,
    )
    assert proc1.returncode == 0
    first = learned.read_text()

    proc2 = subprocess.run(
        [sys.executable, str(CLI_PATH), "refresh-anchor-baseline"],
        env=env, capture_output=True, text=True, timeout=10,
    )
    assert proc2.returncode == 0
    second = learned.read_text()
    assert first == second
