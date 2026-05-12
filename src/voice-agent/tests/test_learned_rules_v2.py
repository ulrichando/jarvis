"""Tests for the v2 learned-rules loader and prompt_builder dispatch."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest


ANCHOR = """\
---
schema_version: 2
---

## ═══ ANCHOR ═══

- <!-- id=A-0001 tier=anchor --> Bare-vocative pings reply "Yes?".
"""


def _learned_with_sha(sha: str) -> str:
    return f"""\
---
schema_version: 2
anchor_baseline_sha256: {sha}
---

# JARVIS Learned Rules

## ═══ CORE ═══

- <!-- id=R-0001 tier=core created=2026-04-30 --> Always use --profile-directory=Default when launching Chrome.

## ═══ ACCEPTED ═══

- <!-- id=R-0002 tier=accepted created=2026-05-09 --> When called by name, answer "Yes?".

## ═══ STAGED ═══

- <!-- id=R-0003 tier=staged created=2026-05-12 --> [STAGED] Avoid Michael Jackson references unless asked.

## ═══ ARCHIVED ═══

- <!-- id=R-0004 tier=archived retired=2026-05-01 reason=dead_subsystem --> ElevenLabs backup.
"""


@pytest.fixture
def files(tmp_path):
    anchor = tmp_path / "anchor.md"
    anchor.write_text(ANCHOR)
    sha = hashlib.sha256(ANCHOR.encode("utf-8")).hexdigest()
    learned = tmp_path / "learned.md"
    learned.write_text(_learned_with_sha(sha))
    return anchor, learned


def test_v2_block_includes_anchor_then_core_then_accepted(files, monkeypatch):
    anchor, learned = files
    from pipeline import learned_rules_v2 as lrv2

    monkeypatch.setattr(lrv2, "ANCHOR_PATH", anchor)
    monkeypatch.setattr(lrv2, "LEARNED_PATH", learned)

    block = lrv2.load_learned_rules_v2()

    assert "═══ ANCHOR ═══" in block
    assert "═══ CORE ═══" in block
    assert "═══ ACCEPTED ═══" in block
    assert block.index("ANCHOR") < block.index("CORE") < block.index("ACCEPTED")


def test_v2_block_marks_staged_with_prefix(files, monkeypatch):
    anchor, learned = files
    from pipeline import learned_rules_v2 as lrv2

    monkeypatch.setattr(lrv2, "ANCHOR_PATH", anchor)
    monkeypatch.setattr(lrv2, "LEARNED_PATH", learned)

    block = lrv2.load_learned_rules_v2()

    assert "[STAGED]" in block


def test_v2_block_excludes_archived(files, monkeypatch):
    anchor, learned = files
    from pipeline import learned_rules_v2 as lrv2

    monkeypatch.setattr(lrv2, "ANCHOR_PATH", anchor)
    monkeypatch.setattr(lrv2, "LEARNED_PATH", learned)

    block = lrv2.load_learned_rules_v2()
    assert "ElevenLabs" not in block
    assert "═══ ARCHIVED ═══" not in block


def test_prompt_builder_dispatches_to_v2_when_flag_set(files, monkeypatch):
    anchor, learned = files
    monkeypatch.setenv("JARVIS_LEARNED_RULES_V2", "1")
    from pipeline import prompt_builder, learned_rules_v2 as lrv2

    monkeypatch.setattr(lrv2, "ANCHOR_PATH", anchor)
    monkeypatch.setattr(lrv2, "LEARNED_PATH", learned)
    monkeypatch.setattr(prompt_builder, "LEARNED_RULES_PATH", learned)

    block = prompt_builder.load_learned_rules()
    assert "═══ ANCHOR ═══" in block


def test_prompt_builder_falls_back_to_v1_when_flag_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("JARVIS_LEARNED_RULES_V2", raising=False)
    v1 = tmp_path / "learned_v1.md"
    v1.write_text("- [2026-05-09] Reply 'Yes?' to bare Jarvis pings.\n")
    from pipeline import prompt_builder
    monkeypatch.setattr(prompt_builder, "LEARNED_RULES_PATH", v1)

    block = prompt_builder.load_learned_rules()
    assert "Reply 'Yes?'" in block
    # Legacy v1 wrapper uses `═══ LEARNED BEHAVIORAL RULES ═══` but never
    # the v2 tier-headers (ANCHOR / CORE / ACCEPTED / STAGED).
    assert "═══ ANCHOR ═══" not in block
    assert "═══ CORE ═══" not in block
    assert "═══ ACCEPTED ═══" not in block
    assert "═══ STAGED ═══" not in block
