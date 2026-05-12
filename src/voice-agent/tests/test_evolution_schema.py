"""Tests for v2 rule schema parser + serializer."""
from __future__ import annotations

import pytest


SAMPLE_V2 = """\
---
schema_version: 2
generated_at: 2026-05-12T07:55:00Z
anchor_baseline_sha256: 5a3f8c
---

# JARVIS Learned Rules

## ═══ CORE ═══

- <!-- id=R-0007 tier=core created=2026-04-30 reinforced=2026-05-09 turns=[t-1841,t-2003,t-2199] supersedes=[R-0003] proposal=P-0012 evidence="never open chromium for chrome" --> When the user says "Chrome" or "Google Chrome", launch /usr/bin/google-chrome --profile-directory="Default".

## ═══ ACCEPTED ═══

- <!-- id=R-0019 tier=accepted created=2026-05-09 reinforced=2026-05-09 turns=[t-2204] proposal=P-0031 evidence="Pardon? is for didn't-hear, not attention" --> When called by name, answer "Yes?" — never "Pardon?".

## ═══ STAGED ═══

- <!-- id=R-0021 tier=staged created=2026-05-11 reinforced=2026-05-11 turns=[t-2301] proposal=P-0042 evaluator={replay:0/0, redteam:0/10, poll:3/3} shadow_until=2026-05-18 --> [STAGED] Avoid mentioning Michael Jackson unless explicitly asked.

## ═══ ARCHIVED ═══

- <!-- id=R-0003 tier=archived created=2026-04-27 retired=2026-04-30 superseded_by=R-0007 reason=duplicate --> "Google Chrome" means /usr/bin/google-chrome.
"""


def test_parse_returns_one_rule_per_tier():
    from pipeline.evolution.schema import parse_rules_v2

    parsed = parse_rules_v2(SAMPLE_V2)

    assert parsed.frontmatter["schema_version"] == 2
    assert parsed.frontmatter["anchor_baseline_sha256"] == "5a3f8c"
    assert len(parsed.rules) == 4
    by_tier = {r.tier: r for r in parsed.rules}
    assert set(by_tier) == {"core", "accepted", "staged", "archived"}

    core = by_tier["core"]
    assert core.id == "R-0007"
    assert core.turns == ["t-1841", "t-2003", "t-2199"]
    assert core.supersedes == ["R-0003"]
    assert core.proposal == "P-0012"
    assert "open chromium" in core.evidence
    assert core.text.startswith("When the user says")

    archived = by_tier["archived"]
    assert archived.superseded_by == "R-0007"
    assert archived.reason == "duplicate"

    staged = by_tier["staged"]
    assert staged.id == "R-0021"
    assert staged.evaluator == {"replay": "0/0", "redteam": "0/10", "poll": "3/3"}
    assert staged.shadow_until == "2026-05-18"


def test_serialize_round_trips():
    from pipeline.evolution.schema import parse_rules_v2, serialize_rules_v2

    parsed = parse_rules_v2(SAMPLE_V2)
    out = serialize_rules_v2(parsed)
    reparsed = parse_rules_v2(out)

    assert len(reparsed.rules) == len(parsed.rules)
    for a, b in zip(
        sorted(parsed.rules, key=lambda r: r.id),
        sorted(reparsed.rules, key=lambda r: r.id),
    ):
        assert a.id == b.id
        assert a.tier == b.tier
        assert a.text == b.text
        assert a.turns == b.turns


def test_parse_rejects_anchor_in_main_file():
    from pipeline.evolution.schema import parse_rules_v2, SchemaError

    bad = SAMPLE_V2.replace(
        "## ═══ CORE ═══",
        "## ═══ ANCHOR ═══\n\n- <!-- id=A-X tier=anchor --> bogus\n\n## ═══ CORE ═══",
    )
    with pytest.raises(SchemaError, match="anchor"):
        parse_rules_v2(bad, allow_anchor=False)


def test_parse_accepts_anchor_when_allowed():
    from pipeline.evolution.schema import parse_rules_v2

    anchor_file = """\
---
schema_version: 2
---

## ═══ ANCHOR ═══

- <!-- id=A-0001 tier=anchor --> "Jarvis" replies "Yes?".
"""
    parsed = parse_rules_v2(anchor_file, allow_anchor=True)
    assert len(parsed.rules) == 1
    assert parsed.rules[0].tier == "anchor"
    assert parsed.rules[0].id == "A-0001"


def test_parse_handles_malformed_metadata_gracefully():
    from pipeline.evolution.schema import parse_rules_v2

    bad_meta = """\
---
schema_version: 2
---

## ═══ ACCEPTED ═══

- <!-- id=R-0099 tier=accepted broken_field --> Rule with a malformed metadata token.
"""
    parsed = parse_rules_v2(bad_meta)
    assert len(parsed.rules) == 1
    assert parsed.rules[0].id == "R-0099"
    assert parsed.rules[0].text.startswith("Rule with a malformed")


def test_serialize_evaluator_round_trips():
    from pipeline.evolution.schema import (
        ParsedRules, Rule, parse_rules_v2, serialize_rules_v2,
    )

    original = Rule(
        id="R-0050",
        tier="staged",
        text="[STAGED] test rule",
        evaluator={"replay": "5/5", "redteam": "10/10", "poll": "3/3"},
        shadow_until="2026-05-19",
    )
    parsed = ParsedRules(
        frontmatter={"schema_version": 2}, rules=[original],
    )
    out = serialize_rules_v2(parsed)
    reparsed = parse_rules_v2(out)

    assert len(reparsed.rules) == 1
    r = reparsed.rules[0]
    assert r.evaluator == original.evaluator
    assert r.shadow_until == original.shadow_until
