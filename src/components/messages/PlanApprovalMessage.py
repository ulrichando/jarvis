"""Plan approval message formatting for terminal.

Formats plan approval prompts and responses for the agent planning system.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Optional

CYAN = "\033[36m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"


@dataclass
class PlanApprovalRequestProps:
    """Properties for a plan approval request."""
    plan_summary: str
    steps: list[str]
    risk_level: str = "medium"
    agent_name: str = ""


@dataclass
class PlanApprovalResponseProps:
    """Properties for a plan approval response."""
    approved: bool
    comment: str = ""


def PlanApprovalRequestDisplay(
    plan_summary: str,
    steps: list[str] | None = None,
    risk_level: str = "medium",
    agent_name: str = "",
) -> str:
    """Format a plan approval request for terminal display.

    Shows the plan summary, steps, and prompts for approval.

    Args:
        plan_summary: Brief description of the plan.
        steps: List of planned steps.
        risk_level: Risk classification.
        agent_name: Name of the agent proposing the plan.

    Returns:
        Formatted multi-line string.
    """
    steps = steps or []
    risk_colors = {"low": GREEN, "medium": YELLOW, "high": RED}
    risk_color = risk_colors.get(risk_level, YELLOW)

    lines = [
        "",
        f"{BOLD}{CYAN}--- Plan Approval Required ---{RESET}",
    ]

    if agent_name:
        lines.append(f"  {DIM}Agent:{RESET} {agent_name}")

    lines.append(f"  {BOLD}Plan:{RESET} {plan_summary}")
    lines.append(f"  {BOLD}Risk:{RESET} {risk_color}{risk_level.upper()}{RESET}")

    if steps:
        lines.append(f"  {BOLD}Steps:{RESET}")
        for i, step in enumerate(steps, 1):
            lines.append(f"    {DIM}{i}.{RESET} {step}")

    lines.append("")
    lines.append(
        f"  {GREEN}[y]{RESET}es, proceed  "
        f"{RED}[n]{RESET}o, cancel  "
        f"{YELLOW}[e]{RESET}dit plan"
    )
    lines.append(f"{BOLD}{CYAN}------------------------------{RESET}")
    lines.append("")

    return "\n".join(lines)


def PlanApprovalResponseDisplay(approved: bool, comment: str = "") -> str:
    """Format a plan approval response.

    Args:
        approved: Whether the plan was approved.
        comment: Optional comment from the user.

    Returns:
        Formatted response string.
    """
    if approved:
        msg = f"{GREEN}Plan approved.{RESET}"
    else:
        msg = f"{RED}Plan rejected.{RESET}"

    if comment:
        msg += f" {DIM}{comment}{RESET}"
    return msg


def tryRenderPlanApprovalMessage(message: dict[str, Any]) -> Optional[str]:
    """Try to render a message as a plan approval, if applicable.

    Args:
        message: Message dict with 'type' and content fields.

    Returns:
        Formatted string if this is a plan approval message, None otherwise.
    """
    msg_type = message.get("type", "")

    if msg_type == "plan_approval_request":
        return PlanApprovalRequestDisplay(
            plan_summary=message.get("summary", ""),
            steps=message.get("steps", []),
            risk_level=message.get("risk_level", "medium"),
            agent_name=message.get("agent_name", ""),
        )
    elif msg_type == "plan_approval_response":
        return PlanApprovalResponseDisplay(
            approved=message.get("approved", False),
            comment=message.get("comment", ""),
        )
    return None


def getPlanApprovalSummary(steps: list[str]) -> str:
    """Generate a brief summary of a plan.

    Args:
        steps: List of planned steps.

    Returns:
        Summary string.
    """
    if not steps:
        return "Empty plan"
    if len(steps) == 1:
        return steps[0]
    return f"{steps[0]} (+{len(steps) - 1} more steps)"


def getIdleNotificationSummary(idle_seconds: float) -> str:
    """Format a notification about agent idle time.

    Args:
        idle_seconds: How long the agent has been idle.

    Returns:
        Formatted idle notification.
    """
    if idle_seconds < 60:
        return f"{DIM}Agent idle for {idle_seconds:.0f}s{RESET}"
    minutes = idle_seconds / 60
    return f"{YELLOW}Agent idle for {minutes:.1f}m{RESET}"


def formatTeammateMessageContent(
    agent_name: str,
    message: str,
    is_notification: bool = False,
) -> str:
    """Format a message from a teammate agent.

    Args:
        agent_name: Name of the teammate.
        message: Message content.
        is_notification: Whether this is a notification vs direct message.

    Returns:
        Formatted message string.
    """
    if is_notification:
        return f"{DIM}[{agent_name}]{RESET} {DIM}{message}{RESET}"
    return f"{CYAN}[{agent_name}]{RESET} {message}"
