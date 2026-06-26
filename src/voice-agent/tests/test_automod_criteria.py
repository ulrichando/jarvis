"""Self-evolution criteria metadata for automod records."""
from __future__ import annotations

import sys
from pathlib import Path

_VOICE_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_VOICE_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_VOICE_AGENT_ROOT))


def test_enrich_record_marks_core_evolution_criteria():
    from pipeline.automod import criteria

    rec = criteria.enrich_record({
        "id": "automod-test",
        "kind": "error",
        "intent": "Fix recurring transcription exception",
        "rationale": "same traceback happened repeatedly",
        "evidence": {"count": 3},
    })

    evo = rec["evolution"]
    assert evo["criteria_version"]
    assert evo["fitness_goal"] == "self_healing"
    assert evo["fitness_goal_label"] == "Self-healing"
    assert evo["perfection_target"]["label"] == "Toward perfect JARVIS"
    assert "no_regressions" in evo["perfection_target"]["fitness_dimensions"]
    assert set(evo["satisfied"]) == {
        "variation",
        "selection",
        "inheritance",
        "feedback",
        "safety",
    }
    assert evo["missing"] == []


def test_enrich_record_classifies_configuration_pressure():
    from pipeline.automod import criteria

    rec = criteria.enrich_record({
        "id": "automod-test",
        "kind": "autonomous",
        "intent": "Tighten tool routing after repeated wrong browser choices",
        "rationale": "repeated routing friction",
    })

    assert rec["evolution"]["fitness_goal"] == "self_configuration"


def test_priority_real_problems_outrank_speculative():
    """All-P0 inflation fix (2026-06-26): speculative self_improvement is now the
    LOWEST priority, so real problems + explicit requests build first."""
    from pipeline.automod import criteria

    def pri(kind):
        return criteria.enrich_record({"id": "x", "kind": kind, "intent": "i"})["priority"]

    assert pri("self_improvement") == "P3"   # speculative → last (was P0)
    assert pri("explicit") == "P0"           # user request → first
    # the cycle sorts ascending (P0 first), so real problems precede speculative:
    assert pri("error") < pri("self_improvement")
    assert pri("correction") < pri("self_improvement")
    assert pri("confab") < pri("self_improvement")
