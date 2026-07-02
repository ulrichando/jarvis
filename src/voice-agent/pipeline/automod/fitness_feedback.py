"""Per-axis fitness feedback for the auto-mod loop (sub-project A, 2026-06-23).

Closes the loop on the fitness signal that already exists: the soak gate scores
five axes per ledger reading (reask / confab / latency / action / interruption)
but only the composite is ever used. This module finds the persistently-weak
axis and turns it into a concrete, file-pointed proposal so the evolution loop
works on what is actually weakest.

Read-only on the ledger; pure functions. `patterns._scan_fitness` is the only
caller and owns dedup + emission.
"""
from __future__ import annotations

import logging
import statistics

logger = logging.getLogger("jarvis.automod.fitness")

# An axis below this in the latest reading, and below it in >= PERSIST_N of the
# last LOOKBACK_M readings, is a weak-axis candidate. Global floor to start;
# add per-axis floors only if it misfires (some axes may run naturally lower).
FITNESS_FLOOR = 0.6
LOOKBACK_M = 5
PERSIST_N = 3

# Learnability (AutoData, 2026-07-02): among weak candidates, prefer the axis
# that OSCILLATES — variance means incremental changes move it (a learnable
# band). An axis flat at the floor (std < FLAT_STD) hasn't responded to
# anything; its proposal is flagged as needing a structurally different
# approach rather than another incremental tweak.
FLAT_STD = 0.02

# A turn slower than this to first word counts as "slow" for latency
# attribution (fallback-involved vs first-try slow).
SLOW_TTFW_MS = 3000

# axis -> (intent template, short rationale label). Templates point at EDITABLE
# files (never the hard-blocklisted soul.md / confab_detector.py / sanitizers) so
# the spawner subagent gets something buildable, not "make latency better".
_AXIS_INTENTS: dict[str, tuple[str, str]] = {
    "latency": (
        "The latency fitness axis sat at {latest:.2f} (floor {floor}) across "
        "{n_below}/{window_m} recent scored windows. Profile the slow turn path "
        "and cut time-to-first-word: look at pipeline/turn_router.py (route "
        "selection), providers/llm.py (prompt-cache hit rate / model pick per "
        "route), and providers/tts.py (TTS start latency). Propose a targeted "
        "reduction without regressing other axes.",
        "latency axis weak",
    ),
    "reask": (
        "The reask fitness axis sat at {latest:.2f} (floor {floor}) across "
        "{n_below}/{window_m} recent windows — JARVIS is re-asking / failing to "
        "advance turns. Tighten clarify + routing: pipeline/turn_router.py "
        "(is_recall_query / clarify routing) and the clarify guidance in "
        "prompts/supervisor.md. Propose a change that lowers the re-ask rate.",
        "reask axis weak",
    ),
    "confab": (
        "The confab fitness axis sat at {latest:.2f} (floor {floor}) across "
        "{n_below}/{window_m} recent windows. Strengthen the anti-confabulation "
        "guidance in prompts/supervisor.md and the tool descriptions so JARVIS "
        "does not claim success without tool evidence. Do NOT touch "
        "confab_detector.py (it is blocklisted) — this is a prompt-strength fix.",
        "confab axis weak",
    ),
    "action": (
        "The action fitness axis sat at {latest:.2f} (floor {floor}) across "
        "{n_below}/{window_m} recent windows — clean-action rate is low. Review "
        "tool-routing guidance in prompts/supervisor.md (prefer the lightest tool "
        "that does the job) so JARVIS reaches for the right tool first time.",
        "action axis weak",
    ),
    "interruption": (
        "The interruption fitness axis sat at {latest:.2f} (floor {floor}) across "
        "{n_below}/{window_m} recent windows. Review barge-in tuning: the "
        "min_duration values in pipeline/turn_router.py::_ROUTE_BASE and the "
        "VAD-direct interrupt handling. Propose a change that improves perceived "
        "interruption handling without raising false barge-ins.",
        "interruption axis weak",
    ),
}


def _axis_values(window: list[dict], axis: str) -> list[float]:
    vals: list[float] = []
    for r in window:
        v = (r.get("per_axis") or {}).get(axis)
        try:
            if v is not None:
                vals.append(float(v))
        except (TypeError, ValueError):
            continue
    return vals


def weak_axis(readings: list[dict]) -> tuple[str, dict] | None:
    """Pick the persistently-weak axis from ledger readings (newest-first).

    Among candidates, prefer the axis with the highest variance over the
    window (learnable — it responds to change); tie-break by lowest latest
    score. Returns (axis, evidence) or None. `evidence` = {axis, latest,
    n_below, window_m, floor, std, flat}.
    """
    window = readings[:LOOKBACK_M]
    if not window:
        return None
    latest_axes = window[0].get("per_axis") or {}
    # (neg_std, latest_score, axis, n_below, std) — sort puts highest-variance
    # first, then lowest latest score.
    candidates: list[tuple[float, float, str, int, float]] = []
    for axis, latest_score in latest_axes.items():
        try:
            latest_val = float(latest_score)
        except (TypeError, ValueError):
            continue
        if latest_val >= FITNESS_FLOOR:
            continue
        vals = _axis_values(window, axis)
        n_below = sum(1 for v in vals if v < FITNESS_FLOOR)
        if n_below >= PERSIST_N:
            std = statistics.pstdev(vals) if len(vals) >= 2 else 0.0
            candidates.append((-std, latest_val, axis, n_below, std))
    if not candidates:
        return None
    candidates.sort()
    _, latest_val, axis, n_below, std = candidates[0]
    evidence = {
        "axis": axis,
        "latest": round(latest_val, 4),
        "n_below": n_below,
        "window_m": len(window),
        "floor": FITNESS_FLOOR,
        "std": round(std, 4),
        "flat": std < FLAT_STD,
    }
    return axis, evidence


def latency_attribution(rows: list[tuple], *, slow_ms: int = SLOW_TTFW_MS) -> dict | None:
    """Split slow turns into fallback-involved vs first-try slow (AutoData's
    attribution lesson: 'stopped timing out' and 'got faster' are different
    work). `rows` = (ttfw_ms, route_fallback, llm_used) for turns already
    filtered to ttfw_ms > slow_ms. Pure; None when there is nothing to split."""
    if not rows:
        return None
    n_fallback = 0
    models: dict[str, int] = {}
    for _ttfw, route_fallback, llm_used in rows:
        if route_fallback:
            n_fallback += 1
        m = str(llm_used or "?")
        models[m] = models.get(m, 0) + 1
    top = sorted(models.items(), key=lambda kv: -kv[1])[:3]
    return {
        "slow_ms": slow_ms,
        "n_slow": len(rows),
        "n_fallback": n_fallback,
        "n_first_try": len(rows) - n_fallback,
        "top_slow_models": [{"model": m, "count": c} for m, c in top],
    }


def build_intent(axis: str, evidence: dict, *, attribution: dict | None = None) -> dict | None:
    """Render a concrete intent + rationale for a weak axis, or None if the axis
    has no actionable mapping (so we never emit a vague 'improve X')."""
    mapped = _AXIS_INTENTS.get(axis)
    if not mapped:
        return None
    template, label = mapped
    intent = template.format(
        latest=evidence.get("latest", 0.0),
        floor=evidence.get("floor", FITNESS_FLOOR),
        n_below=evidence.get("n_below", 0),
        window_m=evidence.get("window_m", 0),
    )
    if evidence.get("flat"):
        intent += (
            f"\n\nNOTE — this axis has been FLAT at this level across the window "
            f"(std {evidence.get('std', 0.0):.3f}). Incremental tweaks have not "
            f"moved it; propose a structurally DIFFERENT approach and state why "
            f"prior incremental attempts plateaued."
        )
    if attribution:
        top = ", ".join(
            f"{t['model']}×{t['count']}" for t in attribution.get("top_slow_models", [])
        )
        intent += (
            f"\n\nLATENCY ATTRIBUTION (last 14d, ttfw>{attribution['slow_ms']}ms): "
            f"{attribution['n_slow']} slow turns — {attribution['n_fallback']} involved "
            f"a route fallback (fix = provider reliability/timeouts), "
            f"{attribution['n_first_try']} were first-try slow (fix = faster primary "
            f"path). Slow-turn models: {top or 'n/a'}. Target the dominant class."
        )
    rationale = (
        f"{label}: {evidence.get('latest', 0.0):.2f} < {evidence.get('floor', FITNESS_FLOOR)} "
        f"in {evidence.get('n_below', 0)}/{evidence.get('window_m', 0)} recent windows"
    )
    return {"intent": intent, "rationale": rationale}
