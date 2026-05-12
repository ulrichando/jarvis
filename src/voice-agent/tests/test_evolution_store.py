"""Tests for the v2 rule store: anchor sha-check + tier-aware ops."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest


ANCHOR_SAMPLE = """\
---
schema_version: 2
---

## ═══ ANCHOR ═══

- <!-- id=A-0001 tier=anchor --> Bare-vocative pings reply "Yes?".
- <!-- id=A-0002 tier=anchor --> Never append sir to replies.
"""

LEARNED_SAMPLE = """\
---
schema_version: 2
anchor_baseline_sha256: PLACEHOLDER
---

# JARVIS Learned Rules

## ═══ ACCEPTED ═══

- <!-- id=R-0001 tier=accepted created=2026-05-09 --> When called by name, answer "Yes?".
"""


@pytest.fixture
def store_paths(tmp_path):
    anchor = tmp_path / "anchor_rules.md"
    learned = tmp_path / "learned_rules.md"
    anchor.write_text(ANCHOR_SAMPLE)
    sha = hashlib.sha256(ANCHOR_SAMPLE.encode("utf-8")).hexdigest()
    learned.write_text(LEARNED_SAMPLE.replace("PLACEHOLDER", sha))
    return anchor, learned, sha


def test_load_validates_anchor_sha(store_paths):
    from pipeline.evolution.store import RuleStore

    anchor, learned, _sha = store_paths
    store = RuleStore(anchor_path=anchor, learned_path=learned)
    rules = store.load()
    ids = {r.id for r in rules.all_rules}
    assert "A-0001" in ids
    assert "R-0001" in ids


def test_load_refuses_when_anchor_sha_mismatches(store_paths):
    from pipeline.evolution.store import RuleStore, AnchorTamperingError

    anchor, learned, _sha = store_paths
    anchor.write_text(ANCHOR_SAMPLE + "\n- <!-- id=A-9999 tier=anchor --> bogus\n")

    store = RuleStore(anchor_path=anchor, learned_path=learned)
    with pytest.raises(AnchorTamperingError):
        store.load()


def test_save_rule_refuses_anchor_tier(store_paths):
    from pipeline.evolution.store import RuleStore, AnchorWriteRefused
    from pipeline.evolution.schema import Rule

    anchor, learned, _sha = store_paths
    store = RuleStore(anchor_path=anchor, learned_path=learned)
    store.load()

    rogue = Rule(id="A-1234", tier="anchor", text="rogue anchor write")
    with pytest.raises(AnchorWriteRefused):
        store.save_rule(rogue)


def test_save_rule_appends_to_correct_section(store_paths):
    from pipeline.evolution.store import RuleStore
    from pipeline.evolution.schema import Rule, parse_rules_v2

    anchor, learned, _sha = store_paths
    store = RuleStore(anchor_path=anchor, learned_path=learned)
    store.load()

    new = Rule(id="R-0002", tier="staged",
               text="[STAGED] don't open chromium for chrome",
               created="2026-05-12")
    store.save_rule(new)

    out = parse_rules_v2(learned.read_text())
    staged_ids = [r.id for r in out.rules if r.tier == "staged"]
    assert staged_ids == ["R-0002"]
    accepted_ids = [r.id for r in out.rules if r.tier == "accepted"]
    assert accepted_ids == ["R-0001"]


def test_update_tier_moves_rule_between_sections(store_paths):
    from pipeline.evolution.store import RuleStore
    from pipeline.evolution.schema import parse_rules_v2

    anchor, learned, _sha = store_paths
    store = RuleStore(anchor_path=anchor, learned_path=learned)
    store.load()

    store.update_tier("R-0001", new_tier="core")

    out = parse_rules_v2(learned.read_text())
    by_tier = {r.id: r.tier for r in out.rules}
    assert by_tier["R-0001"] == "core"


def test_update_tier_refuses_anchor_target(store_paths):
    from pipeline.evolution.store import RuleStore, AnchorWriteRefused

    anchor, learned, _sha = store_paths
    store = RuleStore(anchor_path=anchor, learned_path=learned)
    store.load()

    with pytest.raises(AnchorWriteRefused):
        store.update_tier("R-0001", new_tier="anchor")


def test_anchor_baseline_sha_in_frontmatter_is_refreshed_on_save(store_paths):
    from pipeline.evolution.store import RuleStore
    from pipeline.evolution.schema import Rule, parse_rules_v2

    anchor, learned, original_sha = store_paths
    store = RuleStore(anchor_path=anchor, learned_path=learned)
    store.load()
    store.save_rule(Rule(id="R-0002", tier="staged", text="test"))

    out = parse_rules_v2(learned.read_text())
    assert out.frontmatter["anchor_baseline_sha256"] == original_sha


def test_load_rejects_unknown_tier_in_learned(tmp_path):
    """A schema-version-skew rule should fail loud, not be silently
    demoted to archived. Caught in Task 2.3 code review."""
    from pipeline.evolution.store import RuleStore
    from pipeline.evolution.schema import SchemaError

    anchor = tmp_path / "anchor.md"
    anchor.write_text(ANCHOR_SAMPLE)
    import hashlib
    sha = hashlib.sha256(ANCHOR_SAMPLE.encode("utf-8")).hexdigest()

    learned = tmp_path / "learned.md"
    learned.write_text(
        f"---\nschema_version: 2\nanchor_baseline_sha256: {sha}\n---\n\n"
        "# JARVIS Learned Rules\n\n## ═══ ACCEPTED ═══\n\n"
        "- <!-- id=R-9999 tier=experimental --> rule with unknown tier\n"
    )

    import pytest
    store = RuleStore(anchor_path=anchor, learned_path=learned)
    with pytest.raises(SchemaError, match="unknown tier"):
        store.load()


def test_concurrent_save_rule_does_not_lose_updates(store_paths, tmp_path):
    """Two writers both calling save_rule must not lose either rule.

    Pre-fix: writer A loads state-S, writer B loads state-S in parallel,
    both mutate independently, both os.replace — one update lost.

    Post-fix: writer A holds LOCK_EX on the sibling .lock file; writer B
    blocks, then on acquisition re-reads disk and sees A's commit, so
    its mutation is applied on top of A's, not on top of stale S.
    """
    from pipeline.evolution.store import RuleStore
    from pipeline.evolution.schema import Rule
    import threading

    anchor, learned, _ = store_paths
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def writer(rule_id: str, text: str) -> None:
        try:
            store = RuleStore(anchor_path=anchor, learned_path=learned)
            store.load()
            barrier.wait()
            store.save_rule(Rule(id=rule_id, tier="accepted", text=text,
                                  created="2026-05-12"))
        except BaseException as e:
            errors.append(e)

    t_a = threading.Thread(target=writer, args=("R-9000", "writer A"))
    t_b = threading.Thread(target=writer, args=("R-9001", "writer B"))
    t_a.start(); t_b.start()
    t_a.join(timeout=5); t_b.join(timeout=5)

    assert errors == [], f"writers errored: {errors}"

    final_store = RuleStore(anchor_path=anchor, learned_path=learned)
    loaded = final_store.load()
    ids = {r.id for r in loaded.accepted}
    assert "R-9000" in ids, "writer A's rule was lost"
    assert "R-9001" in ids, "writer B's rule was lost"
    assert "R-0001" in ids, "pre-existing rule was lost"
