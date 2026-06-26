"""Evolution criteria tagging for auto-mod intents.

Attaches a fitness-goal label, satisfied-principle set, and perfection-target
metadata to every emitted intent so the evolution gate can later measure
whether a proposal actually moved the needle on the axis it claimed to improve.

The five Darwinian pillars (variation, selection, inheritance, feedback, safety)
are ALL satisfied by every automod proposal — the architecture provides each of
these independently of the intent kind (see ``_FULL_SATISFIED``, the authoritative
pillar set; the web /evolution UI mirrors it). Three further properties hold at
the *system* level rather than per-proposal and are tracked separately, not in
``satisfied``: visibility (the /evolution review surface), bounded autonomy (the
human-approval ladder), and the perfection target (the fitness dimensions each
proposal claims to move). The per-kind mapping below is about which axis the
proposal primarily targets.

Pure functions — no side effects, no imports beyond stdlib.
"""
from __future__ import annotations

CRITERIA_VERSION = "1.0.0"

# Every automod proposal satisfies all five Darwin+Gödel pillars because the
# architecture guarantees them:
#   variation   — a new proposal branch exists
#   selection   — the test gate + diff validation + human deploy-approval gate
#   inheritance — merge + watchdog persist good changes, roll back bad ones
#   feedback    — user corrections + telemetry signals feed the detector
#   safety      — hard blocklist, watchdog auto-rollback, kill switches
_FULL_SATISFIED = frozenset({
    "variation",
    "selection",
    "inheritance",
    "feedback",
    "safety",
})

_PERFECTION_TARGET = {
    "label": "Toward perfect JARVIS",
    "fitness_dimensions": [
        "no_regressions",
        "lower_reask_rate",
        "higher_confab_quality",
        "lower_latency",
        "higher_clean_action_rate",
    ],
}

# Each pattern kind targets a specific fitness goal.
_KIND_GOAL: dict[str, tuple[str, str]] = {
    "correction":  ("self_configuration", "Self-configuration"),
    "confab":      ("self_protection",     "Self-protection"),
    "error":       ("self_healing",        "Self-healing"),
    "fitness":     ("self_optimization",   "Self-optimization"),
    "explicit":    ("self_configuration", "Self-configuration"),
}

_DEFAULT_GOAL = ("self_configuration", "Self-configuration")

# Build priority. The cycle builds P0 first. Reworked 2026-06-26 (per Ulrich,
# reversing the earlier "self-assessment is top priority"): REAL problems and
# explicit requests outrank SPECULATIVE self-improvements, so the loop fixes
# errors/corrections before chasing its own ideas — and the priority field is
# meaningful again (it had inflated to ~all-P0, since most intents are
# self_improvement). Retries inherit their lineage's priority.
_KIND_PRIORITY = {
    "explicit":         "P0",   # user asked for it directly — highest
    "confab":           "P0",   # confabulation = a safety defect, fix first
    "correction":       "P1",   # repeated user correction (real pain)
    "error":            "P1",   # self-detected runtime error (real)
    "fitness":          "P2",   # weak-axis self-optimization
    "self_improvement": "P3",   # JARVIS's own speculative idea — last
}
_DEFAULT_PRIORITY = "P3"


def enrich_record(record: dict) -> dict:
    """Attach evolution metadata to an intent record before enqueueing.

    Adds ``record["evolution"]`` = {criteria_version, fitness_goal,
    fitness_goal_label, perfection_target, satisfied, missing} in-place.
    Returns the same dict so callers can chain.

    Never raises — unknown kinds get a conservative default.
    """
    kind = record.get("kind", "")
    goal, label = _KIND_GOAL.get(kind, _DEFAULT_GOAL)
    # Stamp build priority (P0 highest). setdefault so an explicitly-set or
    # inherited (retry) priority is preserved.
    record.setdefault("priority", _KIND_PRIORITY.get(kind, _DEFAULT_PRIORITY))
    # source = who initiated this variant. Only an explicit user request is
    # "explicit"; every detector-emitted kind (correction/confab/error) and a
    # self-initiated "autonomous" propose are autonomous selection pressure.
    source = "explicit" if kind == "explicit" else "autonomous"
    record["evolution"] = {
        "criteria_version": CRITERIA_VERSION,
        "fitness_goal": goal,
        "fitness_goal_label": label,
        "perfection_target": dict(_PERFECTION_TARGET),
        "source": source,
        "satisfied": sorted(_FULL_SATISFIED),
        "missing": [],
    }
    return record
