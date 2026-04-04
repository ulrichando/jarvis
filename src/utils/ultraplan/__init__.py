"""Ultraplan utilities -- state management and session helpers for multi-agent planning."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional


# ---------------------------------------------------------------------------
# Phase constants
# ---------------------------------------------------------------------------

PHASE_RESEARCHING = "researching"
PHASE_PLANNING = "planning"
PHASE_NEEDS_INPUT = "needs_input"
PHASE_PLAN_READY = "plan_ready"


# ---------------------------------------------------------------------------
# State helpers (work with AppStateStore)
# ---------------------------------------------------------------------------

def enter_ultraplan_mode(set_app_state: Callable) -> None:
    """Mark the session as being in ultraplan mode."""
    set_app_state(lambda prev: {
        **prev,
        "is_ultraplan_mode": True,
        "ultraplan_launching": True,
    })


def exit_ultraplan_mode(set_app_state: Callable) -> None:
    """Clear all ultraplan state from the session."""
    set_app_state(lambda prev: {
        **prev,
        "is_ultraplan_mode": False,
        "ultraplan_launching": None,
        "ultraplan_session_url": None,
        "ultraplan_pending_choice": None,
        "ultraplan_launch_pending": None,
    })


def set_ultraplan_phase(set_app_state: Callable, phase: str) -> None:
    """Update the current ultraplan phase (shows in pill label)."""
    set_app_state(lambda prev: {
        **prev,
        "ultraplan_launching": False,
    })


def build_ultraplan_task(
    goal: str,
    phase: str = PHASE_RESEARCHING,
) -> Dict[str, Any]:
    """Build a task dict suitable for the pill label system."""
    return {
        "type": "remote_agent",
        "isUltraplan": True,
        "ultraplanPhase": phase,
        "goal": goal,
    }


def format_plan_output(
    goal: str,
    research: str,
    plan: str,
    research_max: int = 500,
) -> str:
    """Format the final ultraplan output for display."""
    research_summary = research[:research_max] + ("..." if len(research) > research_max else "")
    sep = "=" * 50
    return (
        f"Ultra Plan: {goal}\n{sep}\n\n"
        f"Research Summary:\n{research_summary}\n\n"
        f"Plan:\n{plan}"
    )
