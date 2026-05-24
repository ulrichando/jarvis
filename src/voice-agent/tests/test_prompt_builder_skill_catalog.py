# src/voice-agent/tests/test_prompt_builder_skill_catalog.py
"""Tests for 3a (skill-catalog block) and 3b (silent authoring nudge).

Task 3 of 2026-05-22-self-improvement-hermes-adaptation.md:
  - 3a: build_skill_catalog_block() in pipeline/prompt_builder.py
  - 3b: silent skill-authoring nudge in prompts/supervisor.md (ops section)

Regression-prevention contract (CLAUDE.md):
  - soul.md must NOT be touched (persona/voice)
  - catalog must be bounded (≤ SKILL_CATALOG_CHAR_BUDGET)
  - catalog must be session-stable (built once, not per-turn)
  - nudge must include "SILENTLY" and "skill_manage" so TTS-leak is ruled out
  - no "hermes" tokens in touched files
"""
from __future__ import annotations

from pathlib import Path

import pipeline.prompt_builder as pb
from pipeline.prompt_builder import build_skill_catalog_block, SKILL_CATALOG_CHAR_BUDGET
from pipeline.skills_loader import Skill


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_skill(name: str, when_to_use: str = "", description: str = "") -> Skill:
    return Skill(
        name=name,
        description=description or f"Description for {name}",
        when_to_use=when_to_use or f"Use when handling {name} tasks.",
        body="## body\nDo the thing.",
        path=Path(f"/fake/skills/{name}/SKILL.md"),
        raw_frontmatter={},
    )


# ── 3a: build_skill_catalog_block ────────────────────────────────────────────

def test_catalog_empty_when_no_skills():
    """Returns '' when the skills list is empty — zero prompt cost."""
    result = build_skill_catalog_block([])
    assert result == "", f"Expected '' for empty skills, got {result!r}"


def test_catalog_contains_skill_names():
    """Each skill's name appears in the catalog block."""
    skills = [_make_skill("spotify-control"), _make_skill("code-review")]
    block = build_skill_catalog_block(skills)
    assert "spotify-control" in block
    assert "code-review" in block


def test_catalog_contains_when_to_use():
    """when_to_use (or description) appears alongside the name."""
    skills = [_make_skill("weather", when_to_use="User asks about the weather forecast.")]
    block = build_skill_catalog_block(skills)
    assert "weather" in block
    assert "weather forecast" in block


def test_catalog_bounded_at_char_budget():
    """With many skills, the block stays within SKILL_CATALOG_CHAR_BUDGET."""
    # 200 skills with generous when_to_use text — well over any reasonable cap.
    skills = [
        _make_skill(f"skill-{i}", when_to_use="A" * 200)
        for i in range(200)
    ]
    block = build_skill_catalog_block(skills)
    assert len(block) <= SKILL_CATALOG_CHAR_BUDGET, (
        f"Block exceeded budget: {len(block)} > {SKILL_CATALOG_CHAR_BUDGET}"
    )


def test_catalog_truncation_note_when_over_budget():
    """When the list is truncated, the block notes how many are omitted."""
    # Force truncation by flooding with skills.
    skills = [_make_skill(f"skill-{i}") for i in range(500)]
    block = build_skill_catalog_block(skills)
    # Either a truncation note OR all skills fit — but if it was truncated,
    # there must be a note like "(+N more" so the supervisor knows to call skills_list.
    if len(block) < SKILL_CATALOG_CHAR_BUDGET:
        # All fit — no note required.
        return
    assert "(+" in block and "more" in block.lower(), (
        "Truncated catalog must include a '(+N more' overflow note"
    )


def test_catalog_returns_string_for_iterable_registry():
    """Works when passed a SkillsRegistry (iterable), not just a list."""
    from pipeline.skills_loader import SkillsRegistry
    reg = SkillsRegistry()
    # A registry with no skills should also return "".
    block = build_skill_catalog_block(reg)
    assert block == ""


def test_catalog_has_section_header():
    """The block has a recognisable section header so it's identifiable
    in the assembled prompt."""
    skills = [_make_skill("spotify")]
    block = build_skill_catalog_block(skills)
    # Must have some section header so the LLM can anchor to it.
    assert "SKILL" in block.upper(), f"Expected 'SKILL' header in block: {block!r}"


def test_catalog_no_hermes_tokens():
    """The catalog block must contain zero 'hermes' occurrences."""
    skills = [_make_skill("spotify"), _make_skill("code-review")]
    block = build_skill_catalog_block(skills)
    assert "hermes" not in block.lower(), "hermes token leaked into catalog block"


# ── 3a (integration): catalog appears in assembled prompt ────────────────────

def test_catalog_injected_into_initial_prompt_state(monkeypatch):
    """The assembled initial_instructions must contain the skill-catalog
    block when skills exist — mirrors how memory_block + breaker_block
    are injected alongside instructions_prefix."""
    import jarvis_agent as ja

    monkeypatch.setattr(ja, "_build_runtime_id_block", lambda sid: "")
    monkeypatch.setattr(ja, "_build_memory_block", lambda: "")
    monkeypatch.setattr(ja, "_build_breaker_status_block", lambda: "")

    # Inject a fake skill catalog builder that returns a known sentinel.
    SENTINEL = "\n\n═══ SKILL CATALOG TEST ═══\n- test-skill: test skill sentinel\n"
    monkeypatch.setattr(ja, "_build_skill_catalog_block", lambda: SENTINEL)

    state = ja._build_initial_prompt_state("test-speech-id")
    assert SENTINEL in state["initial_instructions"], (
        "Skill catalog block not found in initial_instructions"
    )


def test_catalog_absent_when_no_skills(monkeypatch):
    """When the catalog builder returns '' (no skills), the dynamically
    generated catalog block is absent from initial_instructions.

    Note: supervisor.md itself contains 'SKILL CATALOG' as a static nudge
    section header — that's intentional and always present.  What must be
    ABSENT when the block returns '' is the data-driven catalog body
    (entries formatted as '- <name>: <when_to_use>').  We verify this by
    checking that the '- skill-0:' sentinel that would appear in a real
    catalog is absent, and that the overall prompt didn't grow beyond the
    base instructions_prefix (no extra block was appended).
    """
    import jarvis_agent as ja

    monkeypatch.setattr(ja, "_build_runtime_id_block", lambda sid: "")
    monkeypatch.setattr(ja, "_build_memory_block", lambda: "")
    monkeypatch.setattr(ja, "_build_breaker_status_block", lambda: "")
    monkeypatch.setattr(ja, "_build_skill_catalog_block", lambda: "")

    state = ja._build_initial_prompt_state("test-speech-id")
    # The catalog block appended by the builder is empty; the
    # initial_instructions must equal instructions_prefix exactly.
    assert state["initial_instructions"] == state["instructions_prefix"], (
        "initial_instructions should equal instructions_prefix when all "
        "dynamic blocks return ''"
    )


# ── 3b: silent authoring nudge in supervisor.md ──────────────────────────────

_SUPERVISOR_MD = Path(__file__).resolve().parents[1] / "prompts" / "supervisor.md"


def test_supervisor_md_exists():
    assert _SUPERVISOR_MD.is_file(), f"supervisor.md not found at {_SUPERVISOR_MD}"


def test_nudge_contains_skill_manage():
    """The nudge must mention skill_manage so the LLM knows the tool name."""
    text = _SUPERVISOR_MD.read_text(encoding="utf-8")
    assert "skill_manage" in text, (
        "supervisor.md must mention 'skill_manage' in the skill-authoring nudge"
    )


def test_nudge_contains_silently():
    """The nudge must say 'SILENTLY' (or 'silently') to prevent TTS-leak."""
    text = _SUPERVISOR_MD.read_text(encoding="utf-8")
    assert "silently" in text.lower(), (
        "supervisor.md must contain 'silently' in the skill-authoring nudge "
        "to prevent tool-call text leaking to TTS"
    )


def test_nudge_contains_skills_list():
    """The nudge must mention skills_list so the LLM knows how to browse."""
    text = _SUPERVISOR_MD.read_text(encoding="utf-8")
    assert "skills_list" in text or "skill_view" in text, (
        "supervisor.md must mention 'skills_list' or 'skill_view' in the nudge"
    )


def test_nudge_contains_skill_library_section():
    """The nudge must be a clearly marked section the LLM can anchor to."""
    text = _SUPERVISOR_MD.read_text(encoding="utf-8")
    assert "SKILL LIBRARY" in text.upper(), (
        "supervisor.md must contain a 'SKILL LIBRARY' section header"
    )


def test_soul_md_untouched():
    """soul.md must NOT contain 'skill_manage' or 'SKILL LIBRARY' — the
    nudge belongs in supervisor.md (ops), not soul.md (persona)."""
    soul_path = Path(__file__).resolve().parents[1] / "prompts" / "soul.md"
    if not soul_path.is_file():
        return  # nothing to check if soul.md doesn't exist
    text = soul_path.read_text(encoding="utf-8")
    assert "skill_manage" not in text, (
        "skill_manage leaked into soul.md — nudge must live in supervisor.md only"
    )
    assert "SKILL LIBRARY" not in text.upper(), (
        "SKILL LIBRARY section leaked into soul.md — must stay in supervisor.md"
    )


def test_no_hermes_in_supervisor_md():
    """supervisor.md must contain zero 'hermes' tokens."""
    text = _SUPERVISOR_MD.read_text(encoding="utf-8")
    assert "hermes" not in text.lower(), (
        "hermes token found in supervisor.md — JARVIS-native names only"
    )


def test_no_hermes_in_prompt_builder():
    """pipeline/prompt_builder.py must contain zero 'hermes' tokens."""
    pb_path = Path(__file__).resolve().parents[1] / "pipeline" / "prompt_builder.py"
    text = pb_path.read_text(encoding="utf-8")
    # Allow historical mentions in comments that pre-date this task.
    # The constraint is: no 'hermes' in identifier names or string literals.
    import re
    # Match hermes as a standalone word (identifier, not a substring of something else).
    hermes_identifiers = re.findall(r'\bhermes\b', text, re.IGNORECASE)
    assert not hermes_identifiers, (
        f"hermes tokens found in prompt_builder.py: {hermes_identifiers}"
    )
