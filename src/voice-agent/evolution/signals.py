"""Pure signal extraction over telemetry rows → WindowSignals.

No I/O, no DB, no import-time side effects. Live-grounded against the
real `confab_check_state` vocabulary (pipeline/turn_telemetry.py:38-70).
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from statistics import median
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
    median_ttfw_ms: float
    clean_action_rate: float  # clean_tool_called / (clean_tool_called + no_text_*) ; 1.0 if none
    interruption_rate: float

    def as_dict(self) -> dict:
        return asdict(self)


def _norm(s: Optional[str]) -> str:
    return "".join(ch for ch in (s or "").lower() if ch.isalnum() or ch == " ").strip()


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
    median_ttfw_ms = float(median(ttfws)) if ttfws else 0.0
    return WindowSignals(n, len(checked), reask_rate, confab_quality, median_ttfw_ms,
                         clean_action_rate, interruption_rate)
