"""Track 2.5 — procedure catalog block + fuzzy intent match (Spec 2026-05-24).

Mirrors test_prompt_builder_skill_catalog.py shape.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_VOICE_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_VOICE_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_VOICE_AGENT_ROOT))


def test_build_procedure_catalog_block_empty():
    """Empty procedures → empty block."""
    from pipeline.prompt_builder import build_procedure_catalog_block
    assert build_procedure_catalog_block([]) == ""


def test_build_procedure_catalog_block_lists_names():
    """Block lists each procedure's name + first step preview."""
    from pipeline.prompt_builder import build_procedure_catalog_block
    procedures = [
        {"name": "deploy-app", "steps": ["pytest", "git push", "check CI"]},
        {"name": "morning-routine", "steps": ["coffee", "shower"]},
    ]
    block = build_procedure_catalog_block(procedures)
    assert "deploy-app" in block
    assert "morning-routine" in block


def test_build_procedure_catalog_block_handles_empty_steps():
    """A procedure with no steps is included but with no preview."""
    from pipeline.prompt_builder import build_procedure_catalog_block
    procedures = [{"name": "broken", "steps": []}]
    block = build_procedure_catalog_block(procedures)
    assert "broken" in block


def test_find_matching_procedure_exact_substring():
    from pipeline.prompt_builder import find_matching_procedure
    procedures = [
        {"name": "deploy-app", "steps": ["a", "b"]},
        {"name": "morning-routine", "steps": ["c"]},
    ]
    match = find_matching_procedure("Jarvis, run deploy-app", procedures)
    assert match is not None
    assert match["name"] == "deploy-app"


def test_find_matching_procedure_fuzzy_chunk():
    """'deploy' (a chunk of 'deploy-app') matches within Levenshtein ≤ 3."""
    from pipeline.prompt_builder import find_matching_procedure
    procedures = [{"name": "deploy-app", "steps": ["a"]}]
    match = find_matching_procedure("Jarvis, deploy the app", procedures)
    assert match is not None
    assert match["name"] == "deploy-app"


def test_find_matching_procedure_no_match():
    from pipeline.prompt_builder import find_matching_procedure
    procedures = [{"name": "deploy-app", "steps": ["a"]}]
    match = find_matching_procedure("what's the weather", procedures)
    assert match is None


def test_find_matching_procedure_empty_inputs():
    from pipeline.prompt_builder import find_matching_procedure
    assert find_matching_procedure("", [{"name": "x", "steps": []}]) is None
    assert find_matching_procedure("anything", []) is None
    assert find_matching_procedure("", []) is None
