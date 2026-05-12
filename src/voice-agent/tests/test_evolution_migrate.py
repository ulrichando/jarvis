"""Tests for v1 (dated bullets) → v2 schema migration."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest


V1_SAMPLE = """\
- [2026-04-27] When the user says "Chrome", launch /usr/bin/google-chrome.
- [2026-04-27] Add ElevenLabs as an extra backup for speech synthesis.
- [2026-04-30] When opening Chrome ALWAYS pass --profile-directory="Default".
- [2026-05-09] When called by name, answer "Yes?" — never "Pardon?".
- [2026-05-09] Ulrich's wife's name is Lizzie.
"""

ANCHOR_SAMPLE = """\
## ═══ ANCHOR ═══

- <!-- id=A-0001 tier=anchor --> dummy anchor.
"""


def test_migration_assigns_ids_and_dates(tmp_path):
    from pipeline.evolution import migrate

    v1 = tmp_path / "learned_rules_v1.md"
    v1.write_text(V1_SAMPLE)
    anchor = tmp_path / "anchor.md"
    anchor.write_text(ANCHOR_SAMPLE)
    out_path = tmp_path / "learned_rules_v2.md"

    migrate.migrate_v1_to_v2(v1_path=v1, anchor_path=anchor, out_path=out_path)

    text = out_path.read_text()
    assert "schema_version: 2" in text
    assert "anchor_baseline_sha256:" in text
    sha = hashlib.sha256(ANCHOR_SAMPLE.encode()).hexdigest()
    assert sha in text

    from pipeline.evolution.schema import parse_rules_v2
    parsed = parse_rules_v2(text)
    ids = sorted(r.id for r in parsed.rules)
    assert ids == ["R-0001", "R-0002", "R-0003", "R-0004", "R-0005"]
    by_id = {r.id: r for r in parsed.rules}
    assert by_id["R-0001"].created == "2026-04-27"
    assert by_id["R-0004"].text.startswith("When called by name")


def test_migration_archives_dead_subsystem_refs(tmp_path):
    from pipeline.evolution import migrate
    from pipeline.evolution.schema import parse_rules_v2

    v1 = tmp_path / "v1.md"
    v1.write_text(V1_SAMPLE)
    anchor = tmp_path / "anchor.md"
    anchor.write_text(ANCHOR_SAMPLE)
    out_path = tmp_path / "v2.md"

    migrate.migrate_v1_to_v2(v1_path=v1, anchor_path=anchor, out_path=out_path)

    parsed = parse_rules_v2(out_path.read_text())
    archived = [r for r in parsed.rules if r.tier == "archived"]
    archived_text = " ".join(r.text for r in archived)
    assert "ElevenLabs" in archived_text
    for r in archived:
        if "ElevenLabs" in r.text:
            assert r.reason == "dead_subsystem"


def test_migration_deduplicates_near_duplicates(tmp_path):
    from pipeline.evolution import migrate
    from pipeline.evolution.schema import parse_rules_v2

    dup_v1 = """\
- [2026-05-05] When the user says 'save that in Maya', save the current browser interaction for next time.
- [2026-05-05] When the user says 'save that in Maya', save the current browser interaction for next time.
"""
    v1 = tmp_path / "v1.md"
    v1.write_text(dup_v1)
    anchor = tmp_path / "anchor.md"
    anchor.write_text(ANCHOR_SAMPLE)
    out_path = tmp_path / "v2.md"

    migrate.migrate_v1_to_v2(v1_path=v1, anchor_path=anchor, out_path=out_path)

    parsed = parse_rules_v2(out_path.read_text())
    accepted = [r for r in parsed.rules if r.tier == "accepted"]
    archived = [r for r in parsed.rules if r.tier == "archived"]
    assert len(accepted) == 1
    assert len(archived) == 1
    assert archived[0].superseded_by == accepted[0].id
    assert archived[0].reason == "duplicate"


def test_migration_is_idempotent(tmp_path):
    from pipeline.evolution import migrate

    v1 = tmp_path / "v1.md"
    v1.write_text(V1_SAMPLE)
    anchor = tmp_path / "anchor.md"
    anchor.write_text(ANCHOR_SAMPLE)
    out_path = tmp_path / "v2.md"

    migrate.migrate_v1_to_v2(v1_path=v1, anchor_path=anchor, out_path=out_path)
    first = out_path.read_text()
    migrate.migrate_v1_to_v2(v1_path=v1, anchor_path=anchor, out_path=out_path)
    second = out_path.read_text()

    assert first == second


def test_migration_preserves_v2_only_rules_on_rerun(tmp_path):
    """Regression: pre-fix, a re-run of the migrator dropped any rules
    that existed in the v2 file but not in the v1 source. Once
    self-evolution begins writing runtime rules, that would erase them."""
    from pipeline.evolution import migrate
    from pipeline.evolution.schema import (
        ParsedRules, Rule, parse_rules_v2, serialize_rules_v2,
    )

    v1 = tmp_path / "v1.md"
    v1.write_text(V1_SAMPLE)
    anchor = tmp_path / "anchor.md"
    anchor.write_text(ANCHOR_SAMPLE)
    out_path = tmp_path / "v2.md"

    migrate.migrate_v1_to_v2(v1_path=v1, anchor_path=anchor, out_path=out_path)
    parsed = parse_rules_v2(out_path.read_text())
    parsed.rules.append(Rule(
        id="R-9999", tier="staged",
        text="[STAGED] runtime-evolution-added rule",
        created="2026-05-15",
    ))
    out_path.write_text(serialize_rules_v2(parsed))

    migrate.migrate_v1_to_v2(v1_path=v1, anchor_path=anchor, out_path=out_path)

    after = parse_rules_v2(out_path.read_text())
    ids = {r.id for r in after.rules}
    assert "R-9999" in ids, (
        "migrator dropped a v2-only rule on re-run — would erase "
        "self-evolution-added runtime rules"
    )


def test_migration_does_not_dead_subsystem_match_comma_sir_in_quoted_phrase(tmp_path):
    """Regression: pre-fix the unbounded ', sir' substring would flag any
    rule mentioning a comma followed by 'sir' anywhere in any context."""
    from pipeline.evolution import migrate
    from pipeline.evolution.schema import parse_rules_v2

    benign = """\
- [2026-05-12] Treat the phrase 'Hi, sir' the way you treat any other casual greeting.
"""
    v1 = tmp_path / "v1.md"
    v1.write_text(benign)
    anchor = tmp_path / "anchor.md"
    anchor.write_text(ANCHOR_SAMPLE)
    out_path = tmp_path / "v2.md"

    migrate.migrate_v1_to_v2(v1_path=v1, anchor_path=anchor, out_path=out_path)
    parsed = parse_rules_v2(out_path.read_text())
    accepted = [r for r in parsed.rules if r.tier == "accepted"]
    assert len(accepted) == 1, (
        f"expected the rule to be accepted (not flagged as dead subsystem), "
        f"got tiers: {[(r.id, r.tier, r.reason) for r in parsed.rules]}"
    )


def test_migration_propagates_schema_error_on_corrupt_v2(tmp_path):
    """The v2 file's SchemaError must propagate — silent overwrite of
    a corrupted v2 would lose data. Only FileNotFoundError /
    UnicodeDecodeError should be swallowed."""
    from pipeline.evolution import migrate
    from pipeline.evolution.schema import SchemaError

    v1 = tmp_path / "v1.md"
    v1.write_text(V1_SAMPLE)
    anchor = tmp_path / "anchor.md"
    anchor.write_text(ANCHOR_SAMPLE)
    out_path = tmp_path / "v2.md"

    out_path.write_text(
        "---\nschema_version: 2\n---\n\n## ═══ ANCHOR ═══\n\n"
        "- <!-- id=A-X tier=anchor --> illegal anchor in non-anchor file\n"
    )

    import pytest
    with pytest.raises(SchemaError):
        migrate.migrate_v1_to_v2(v1_path=v1, anchor_path=anchor, out_path=out_path)
