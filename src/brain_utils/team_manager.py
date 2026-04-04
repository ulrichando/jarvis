"""Team management utilities for JARVIS.

Handles agent team composition, status display, and rule-based
team suggestions for multi-agent coordination.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal, Optional


AgentType = Literal["scout", "worker", "planner", "reviewer", "specialist"]
TeamStrategy = Literal["pipeline", "parallel", "swarm"]
TeamStatus = Literal["idle", "running", "completed", "failed"]
MemberStatus = Literal["idle", "working", "done", "error", "stopped"]


@dataclass
class TeamMember:
    """A single agent within a team."""

    agent_type: AgentType
    role: str
    status: MemberStatus = "idle"
    task_assigned: Optional[str] = None


@dataclass
class Team:
    """A named team of agents working toward a shared goal."""

    name: str
    goal: str
    members: list[TeamMember] = field(default_factory=list)
    strategy: TeamStrategy = "parallel"
    status: TeamStatus = "idle"
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = (
                datetime.now(timezone.utc).isoformat(timespec="seconds")
            )


# -- Status icons -------------------------------------------------------------

_MEMBER_ICONS: dict[MemberStatus, str] = {
    "idle": "o",
    "working": ">",
    "done": "+",
    "error": "x",
    "stopped": "-",
}

_STRATEGY_LABELS: dict[TeamStrategy, str] = {
    "pipeline": "Pipeline (sequential)",
    "parallel": "Parallel (concurrent)",
    "swarm": "Swarm (autonomous)",
}


def format_team_status(team: Team) -> str:
    """Format team status with member details for CLI display.

    Args:
        team: Team instance to format.

    Returns:
        Multi-line string describing the team and its members.
    """
    lines: list[str] = []

    # Header
    lines.append(f"Team: {team.name}")
    lines.append(f"  Goal: {team.goal}")
    lines.append(f"  Strategy: {_STRATEGY_LABELS.get(team.strategy, team.strategy)}")
    lines.append(f"  Status: {team.status}")
    lines.append("")

    # Members
    if not team.members:
        lines.append("  No members assigned.")
    else:
        total = len(team.members)
        done = sum(1 for m in team.members if m.status == "done")
        lines.append(f"  Members ({done}/{total} done):")
        for m in team.members:
            icon = _MEMBER_ICONS.get(m.status, "?")
            task = f" -- {m.task_assigned}" if m.task_assigned else ""
            lines.append(f"    [{icon}] {m.agent_type} ({m.role}){task}")

    return "\n".join(lines)


# -- Rule-based team suggestion -----------------------------------------------

# Keyword patterns mapped to suggested agent roles
_GOAL_PATTERNS: list[tuple[list[str], list[dict]]] = [
    (
        ["review", "audit", "check", "inspect", "security"],
        [
            {"agent_type": "scout", "role": "gather files and context"},
            {"agent_type": "reviewer", "role": "analyze and report findings"},
        ],
    ),
    (
        ["refactor", "rewrite", "migrate", "convert", "port"],
        [
            {"agent_type": "planner", "role": "design refactoring plan"},
            {"agent_type": "worker", "role": "apply code changes"},
            {"agent_type": "reviewer", "role": "verify correctness"},
        ],
    ),
    (
        ["build", "implement", "create", "develop", "add feature"],
        [
            {"agent_type": "planner", "role": "break down requirements"},
            {"agent_type": "worker", "role": "implement changes"},
            {"agent_type": "reviewer", "role": "review implementation"},
        ],
    ),
    (
        ["test", "qa", "validate"],
        [
            {"agent_type": "scout", "role": "discover existing tests"},
            {"agent_type": "worker", "role": "write and run tests"},
        ],
    ),
    (
        ["debug", "fix", "troubleshoot", "investigate"],
        [
            {"agent_type": "scout", "role": "collect logs and reproduce"},
            {"agent_type": "specialist", "role": "diagnose root cause"},
            {"agent_type": "worker", "role": "apply fix"},
        ],
    ),
    (
        ["document", "docs", "explain"],
        [
            {"agent_type": "scout", "role": "gather code context"},
            {"agent_type": "worker", "role": "write documentation"},
        ],
    ),
    (
        ["deploy", "release", "ship"],
        [
            {"agent_type": "planner", "role": "plan deployment steps"},
            {"agent_type": "worker", "role": "execute deployment"},
            {"agent_type": "reviewer", "role": "verify deployment"},
        ],
    ),
    (
        ["search", "find", "scan", "explore", "analyze"],
        [
            {"agent_type": "scout", "role": "search and collect data"},
            {"agent_type": "planner", "role": "synthesize findings"},
        ],
    ),
]


def suggest_team_composition(goal: str) -> list[dict]:
    """Suggest agent types and roles for a goal using rule-based matching.

    Scans the goal string for keyword patterns and returns a list of
    suggested team member dicts. Falls back to a generic scout+worker
    pair if no specific pattern matches.

    Args:
        goal: Natural language description of the team's objective.

    Returns:
        List of dicts with 'agent_type' and 'role' keys.
    """
    goal_lower = goal.lower()

    best_match: list[dict] | None = None
    best_score = 0

    for keywords, composition in _GOAL_PATTERNS:
        score = sum(1 for kw in keywords if kw in goal_lower)
        if score > best_score:
            best_score = score
            best_match = composition

    if best_match:
        return [dict(m) for m in best_match]

    # Fallback: generic composition
    return [
        {"agent_type": "scout", "role": "gather context and information"},
        {"agent_type": "worker", "role": "execute primary task"},
    ]
