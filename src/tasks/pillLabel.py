"""Produces the compact footer-pill label for a set of background tasks.
Used by both the footer pill and the turn-duration transcript line so the
two surfaces agree on terminology.
"""

from __future__ import annotations

from typing import List

# Diamond figures
DIAMOND_FILLED = "\u25c6"
DIAMOND_OPEN = "\u25c7"


def count(items: list, predicate) -> int:
    """Count items matching a predicate."""
    return sum(1 for item in items if predicate(item))


def get_pill_label(tasks: List[dict]) -> str:
    """Produces the compact footer-pill label for a set of background tasks."""
    n = len(tasks)
    if n == 0:
        return ""

    all_same_type = all(t.get("type") == tasks[0].get("type") for t in tasks)

    if all_same_type:
        task_type = tasks[0].get("type")

        if task_type == "local_bash":
            monitors = count(
                tasks,
                lambda t: t.get("type") == "local_bash" and t.get("kind") == "monitor",
            )
            shells = n - monitors
            parts: List[str] = []
            if shells > 0:
                parts.append("1 shell" if shells == 1 else f"{shells} shells")
            if monitors > 0:
                parts.append("1 monitor" if monitors == 1 else f"{monitors} monitors")
            return ", ".join(parts)

        elif task_type == "in_process_teammate":
            team_count = len(
                set(
                    t.get("identity", {}).get("teamName", "")
                    for t in tasks
                    if t.get("type") == "in_process_teammate"
                )
            )
            return "1 team" if team_count == 1 else f"{team_count} teams"

        elif task_type == "local_agent":
            return "1 local agent" if n == 1 else f"{n} local agents"

        elif task_type == "remote_agent":
            first = tasks[0]
            # Per design mockup: open diamond while running/needs-input,
            # filled once ExitPlanMode is awaiting approval.
            if n == 1 and first.get("isUltraplan"):
                phase = first.get("ultraplanPhase")
                if phase == "plan_ready":
                    return f"{DIAMOND_FILLED} ultraplan ready"
                elif phase == "needs_input":
                    return f"{DIAMOND_OPEN} ultraplan needs your input"
                else:
                    return f"{DIAMOND_OPEN} ultraplan"
            return (
                f"{DIAMOND_OPEN} 1 cloud session"
                if n == 1
                else f"{DIAMOND_OPEN} {n} cloud sessions"
            )

        elif task_type == "local_workflow":
            return (
                "1 background workflow"
                if n == 1
                else f"{n} background workflows"
            )

        elif task_type == "monitor_mcp":
            return "1 monitor" if n == 1 else f"{n} monitors"

        elif task_type == "dream":
            return "dreaming"

    return f"{n} background {'task' if n == 1 else 'tasks'}"


def pill_needs_cta(tasks: List[dict]) -> bool:
    """True when the pill should show the dimmed ' . down to view' call-to-action.
    Per the state diagram: only the two attention states (needs_input,
    plan_ready) surface the CTA; plain running shows just the diamond + label.
    """
    if len(tasks) != 1:
        return False
    t = tasks[0]
    return (
        t.get("type") == "remote_agent"
        and t.get("isUltraplan") is True
        and t.get("ultraplanPhase") is not None
    )
