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

logger = logging.getLogger("jarvis.automod.fitness")

# An axis below this in the latest reading, and below it in >= PERSIST_N of the
# last LOOKBACK_M readings, is a weak-axis candidate. Global floor to start;
# add per-axis floors only if it misfires (some axes may run naturally lower).
FITNESS_FLOOR = 0.6
LOOKBACK_M = 5
PERSIST_N = 3

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


def weak_axis(readings: list[dict]) -> tuple[str, dict] | None:
    """Pick the persistently-weak axis from ledger readings (newest-first).

    Returns (axis, evidence) or None if no axis is weak enough / persistent
    enough. `evidence` = {axis, latest, n_below, window_m, floor}.
    """
    window = readings[:LOOKBACK_M]
    if not window:
        return None
    latest_axes = window[0].get("per_axis") or {}
    candidates: list[tuple[float, str, int]] = []  # (latest_score, axis, n_below)
    for axis, latest_score in latest_axes.items():
        try:
            latest_val = float(latest_score)
        except (TypeError, ValueError):
            continue
        if latest_val >= FITNESS_FLOOR:
            continue
        n_below = 0
        for r in window:
            v = (r.get("per_axis") or {}).get(axis)
            try:
                if v is not None and float(v) < FITNESS_FLOOR:
                    n_below += 1
            except (TypeError, ValueError):
                continue
        if n_below >= PERSIST_N:
            candidates.append((latest_val, axis, n_below))
    if not candidates:
        return None
    candidates.sort()  # lowest latest score first
    latest_val, axis, n_below = candidates[0]
    evidence = {
        "axis": axis,
        "latest": round(latest_val, 4),
        "n_below": n_below,
        "window_m": len(window),
        "floor": FITNESS_FLOOR,
    }
    return axis, evidence


def build_intent(axis: str, evidence: dict) -> dict | None:
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
    rationale = (
        f"{label}: {evidence.get('latest', 0.0):.2f} < {evidence.get('floor', FITNESS_FLOOR)} "
        f"in {evidence.get('n_below', 0)}/{evidence.get('window_m', 0)} recent windows"
    )
    return {"intent": intent, "rationale": rationale}
