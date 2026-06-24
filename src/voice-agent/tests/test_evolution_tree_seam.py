"""Phase 4 — tree-seam guard.

Generalizing the evolution engine to web/desktop is meant to be swapping a
per-tree PROFILE, not rewriting the engine (see the design doc:
docs/superpowers/specs/2026-06-24-evolution-tree-profile-seam.md). That only
holds if every tree-specific site is CATALOGUED. This test pins the catalogue:
the set of engine modules that hardcode the voice-agent tree prefix is fixed.

A NEW module hardcoding ``src/voice-agent/`` is a new tree-specific seam — it
must be added to the catalogue (and, at extraction time, fed from the profile),
so this test fails until the catalogue is updated. The catalogue can't silently
rot between now and the extraction.
"""
from __future__ import annotations

from pathlib import Path

_AUTOMOD = Path(__file__).resolve().parent.parent / "pipeline" / "automod"
_TREE_PREFIX = "src/voice-agent/"

# The catalogued set — verified by grep 2026-06-24. Every engine module that
# hardcodes the tree prefix today (as a constant, a path, a prompt, or a doc
# comment). Keep in sync with the seam spec.
_CATALOGUED_PREFIX_SITES = {
    "_state.py",            # ALLOWED_PATH_PREFIX + tree-specific blocklist
    "coverage_gate.py",     # _SRC_PREFIX
    "error_logger.py",      # _PROJECT_PREFIX (error/trigger source)
    "error_log_fallback.py",
    "finalize.py",          # tree root / interpreter / test command
    "watchdog.py",          # .venv interpreter for the selftest
    "patterns.py",          # agent edit-scope prompt
    "spawner.py",           # agent edit-scope prompt
    "deploy.py",            # module-location doc comment
}


def _modules_with_tree_prefix() -> set[str]:
    return {
        py.name for py in _AUTOMOD.glob("*.py")
        if _TREE_PREFIX in py.read_text(encoding="utf-8")
    }


def test_tree_prefix_hardcodes_match_the_catalogue():
    found = _modules_with_tree_prefix()
    new = found - _CATALOGUED_PREFIX_SITES
    gone = _CATALOGUED_PREFIX_SITES - found
    assert not new, (
        f"New tree-specific hardcode of '{_TREE_PREFIX}' in {sorted(new)} — add it "
        "to docs/superpowers/specs/2026-06-24-evolution-tree-profile-seam.md (and "
        "this set) so generalization stays a config swap, not a rewrite."
    )
    assert not gone, (
        f"Catalogued site(s) {sorted(gone)} no longer hardcode '{_TREE_PREFIX}' — "
        "update the catalogue (extracted to the tree profile already?)."
    )
