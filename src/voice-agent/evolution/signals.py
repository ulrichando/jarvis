"""Pure signal extraction over telemetry rows → WindowSignals.

No I/O, no DB, no import-time side effects. Live-grounded against the
real `confab_check_state` vocabulary (pipeline/turn_telemetry.py:38-70).
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Optional

RE_ASK_WINDOW = 3   # a near-duplicate user utterance within N turns = a re-ask (failure proxy)

# Real confab_check_state vocabulary (pipeline/turn_telemetry.py:38-70).
_RECOVERED = {"caught_t1_passed", "caught_t2_passed", "caught_t3_passed",
              "no_text_t1_passed", "no_text_t2_passed", "no_text_t3_passed"}
_FAILURE   = {"caught_filler", "no_text_filler", "retry_factory_missing",
              "retry_exception", "bypassed_killed"}
_UNCHECKED = {"unchecked", None, ""}


@dataclass
class WindowSignals:
    n_turns: int
    n_checked: int            # turns whose confab gate actually ran
    reask_rate: float         # frac of turns that are a repeat of a recent utterance
    confab_quality: float     # (clean + 0.5*recovered) / checked ; 1.0 if no checked turns
    ttfw_p90_ms: float        # 90th-pct time-to-first-word (p90 not median — a wedge's badness lives in its slow tail)
    clean_action_rate: float  # clean_tool_called / (clean_tool_called + no_text_*) ; 1.0 if none
    interruption_rate: float

    def as_dict(self) -> dict:
        return asdict(self)


def _norm(s: Optional[str]) -> str:
    return "".join(ch for ch in (s or "").lower() if ch.isalnum() or ch == " ").strip()


def _p90(values: list) -> float:
    """90th-percentile (nearest-rank). Unlike a median, this keeps the slow
    tail — a wedged window's badness lives in its outlier-slow turns, which a
    median washes out (live 2026-05-30: the wedged window's median ttfw was
    *lower* than a calm day's, hiding the wedge). 0.0 if no samples."""
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(len(s) - 1, int(round(0.9 * (len(s) - 1))))
    return float(s[idx])


def compute_signals(turns: list[dict]) -> WindowSignals:
    n = len(turns)
    if n == 0:
        return WindowSignals(0, 0, 0.0, 1.0, 0.0, 1.0, 0.0)
    norms = [_norm(t.get("user_text")) for t in turns]
    reasks = sum(1 for i, u in enumerate(norms)
                 if u and u in norms[max(0, i - RE_ASK_WINDOW):i])
    reask_rate = reasks / n
    states = [t.get("confab_check_state") for t in turns]
    checked = [s for s in states if s not in _UNCHECKED]
    if checked:
        clean = sum(1 for s in checked if str(s).startswith("clean"))
        recovered = sum(1 for s in checked if s in _RECOVERED)
        confab_quality = min(1.0, (clean + 0.5 * recovered) / len(checked))
    else:
        confab_quality = 1.0
    tool_clean = sum(1 for s in states if s == "clean_tool_called")
    no_text = sum(1 for s in states if str(s or "").startswith("no_text"))
    clean_action_rate = (tool_clean / (tool_clean + no_text)) if (tool_clean + no_text) else 1.0
    interruption_rate = sum(1 for t in turns if (t.get("interrupted") or 0)) / n
    ttfws = [t["ttfw_ms"] for t in turns if t.get("ttfw_ms")]
    ttfw_p90_ms = _p90(ttfws)
    return WindowSignals(n, len(checked), reask_rate, confab_quality, ttfw_p90_ms,
                         clean_action_rate, interruption_rate)
