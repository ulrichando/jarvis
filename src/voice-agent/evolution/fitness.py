"""Fitness — normalization, lexicographic guardrails, transparent composite.

Pure functions over WindowSignals. No I/O, no DB, no import-time side effects.
The WEIGHTS + GUARDRAILS below are the human-owned "constitution": the composite
is for ranking, the guardrails are for vetoing. (Later increment: this module +
its weights/guardrails go onto the auto-mod HARD_BLOCKLIST_PATHS — the evolver
must never edit its own fitness function.)
"""
from __future__ import annotations
import os
from dataclasses import dataclass
from .signals import WindowSignals

# --- CONSTITUTION: weights (sum 1.0) + guardrail floors. Human-owned; later → blocklist. ---
WEIGHTS = {"reask": 0.35, "confab": 0.25, "latency": 0.20, "action": 0.15, "interruption": 0.05}
# Floors on the NORMALIZED 0..1 sub-scores (higher=better). Interruption is NOT guarded
# (empirically ambiguous — active conversations interrupt more).
GUARDRAILS = {"reask": 0.70, "confab": 0.70}


def _ttfw_target_ms() -> float:
    try:
        return float(os.environ.get("JARVIS_TTFW_TARGET_MS", "1000")) or 1000.0
    except (TypeError, ValueError):
        return 1000.0


@dataclass
class FitnessReading:
    per_axis: dict
    composite: float
    guardrail_flags: dict     # axis -> True if VIOLATED
    passed: bool
    n_turns: int = 0


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def _normalize(sig: WindowSignals) -> dict:
    target = _ttfw_target_ms()
    lat = 1.0 if sig.median_ttfw_ms <= 0 else _clamp(1.0 - (sig.median_ttfw_ms - target) / (3 * target))
    return {
        "reask":        _clamp(1.0 - sig.reask_rate),
        "confab":       _clamp(sig.confab_quality),
        "latency":      _clamp(lat),
        "action":       _clamp(sig.clean_action_rate),
        "interruption": _clamp(1.0 - sig.interruption_rate),
    }


def score(sig: WindowSignals) -> FitnessReading:
    axis = _normalize(sig)
    composite = sum(WEIGHTS[k] * axis[k] for k in WEIGHTS)
    flags = {k: (axis[k] < floor) for k, floor in GUARDRAILS.items()}
    # An empty/no-data window is never "passing" — no evidence to promote on.
    passed = (sig.n_turns > 0) and (not any(flags.values()))
    return FitnessReading(per_axis=axis, composite=round(composite, 4),
                          guardrail_flags=flags, passed=passed, n_turns=sig.n_turns)


def is_fitter(candidate: FitnessReading, incumbent: FitnessReading, min_delta: float = 0.0) -> bool:
    """Fitter iff candidate passes all guardrails AND its composite exceeds incumbent's by
    > min_delta. Guardrail failure disqualifies regardless of composite (lexicographic veto)."""
    if not candidate.passed:
        return False
    return (candidate.composite - incumbent.composite) > min_delta
