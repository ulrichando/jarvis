"""Permission explanation display for terminal.

Explains why a permission is needed, with risk assessment coloring.
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

LOADING_MESSAGE = f"{DIM}Analyzing permission request...{RESET}"


@dataclass
class PermissionExplanationProps:
    """Properties for permission explanation display."""
    tool_name: str
    args: dict[str, Any]
    risk_level: str = "medium"
    explanation: str = ""


@dataclass
class ExplainerState:
    """State of the permission explainer."""
    loading: bool = False
    explanation: str = ""
    risk_level: str = "medium"


def getRiskColor(risk_level: str) -> str:
    """Return ANSI color code for a given risk level.

    Args:
        risk_level: One of 'low', 'medium', 'high'.

    Returns:
        ANSI escape code string.
    """
    return {
        "low": GREEN,
        "medium": YELLOW,
        "high": RED,
    }.get(risk_level, YELLOW)


def getRiskLabel(risk_level: str) -> str:
    """Return human-readable risk label.

    Args:
        risk_level: One of 'low', 'medium', 'high'.

    Returns:
        Risk label string.
    """
    return {
        "low": "Low Risk",
        "medium": "Medium Risk",
        "high": "High Risk",
    }.get(risk_level, "Unknown Risk")


def ShimmerLoadingText(message: str = "Loading...") -> str:
    """Format a loading indicator for the terminal.

    Args:
        message: Text to display while loading.

    Returns:
        Formatted loading string with dim styling.
    """
    return f"{DIM}... {message}{RESET}"


def createExplanationPromise(
    tool_name: str,
    args: dict[str, Any] | None = None,
) -> ExplainerState:
    """Create an explanation state for a tool permission request.

    Synchronously generates an explanation based on the tool name and args.

    Args:
        tool_name: Name of the tool.
        args: Tool arguments.

    Returns:
        ExplainerState with explanation text and risk level.
    """
    args = args or {}
    risk_level = _assess_risk(tool_name, args)
    explanation = _generate_explanation(tool_name, args)
    return ExplainerState(loading=False, explanation=explanation, risk_level=risk_level)


def _assess_risk(tool_name: str, args: dict[str, Any]) -> str:
    """Assess risk level for a tool call."""
    if tool_name == "bash":
        cmd = args.get("command", "")
        high_risk_patterns = [
            "rm -rf", "sudo", "chmod 777", "mkfs", "dd if=",
            "> /dev/", "curl | sh", "wget | sh", "eval ",
        ]
        for pattern in high_risk_patterns:
            if pattern in cmd:
                return "high"
        return "medium"
    elif tool_name == "write_file":
        path = args.get("path", "")
        if any(s in path for s in ["/etc/", "/usr/", "/bin/", ".ssh/", ".env"]):
            return "high"
        return "medium"
    elif tool_name == "edit_file":
        return "medium"
    elif tool_name in ("read_file", "search_files"):
        path = args.get("path", "")
        if any(s in path for s in [".ssh/", ".env", "passwd", "shadow"]):
            return "high"
        return "low"
    elif tool_name in ("web_search", "web_fetch"):
        return "low"
    elif tool_name == "dispatch":
        return "medium"
    return "medium"


def _generate_explanation(tool_name: str, args: dict[str, Any]) -> str:
    """Generate a human-readable explanation of why a permission is needed."""
    if tool_name == "bash":
        cmd = args.get("command", "")
        if "rm " in cmd:
            return "This command deletes files. Verify the target path is correct."
        if "sudo" in cmd:
            return "This command requests superuser privileges."
        if "curl" in cmd or "wget" in cmd:
            return "This command downloads content from the internet."
        return "Shell commands can modify your system. Review the command before allowing."
    elif tool_name == "write_file":
        path = args.get("path", "?")
        return f"This will create or overwrite the file at: {path}"
    elif tool_name == "edit_file":
        path = args.get("path", "?")
        return f"This will modify the file at: {path}"
    elif tool_name == "read_file":
        path = args.get("path", "?")
        return f"This will read the file at: {path}"
    elif tool_name == "web_fetch":
        url = args.get("url", "?")
        return f"This will fetch content from: {url}"
    elif tool_name == "dispatch":
        agent = args.get("agent", "?")
        return f"This will spawn sub-agent '{agent}' with its own tool access."
    return f"Tool '{tool_name}' requires permission to execute."


def usePermissionExplainerUI(
    tool_name: str,
    args: dict[str, Any] | None = None,
) -> str:
    """Get a formatted terminal display for a permission explanation.

    Args:
        tool_name: Name of the tool.
        args: Tool arguments.

    Returns:
        Formatted string.
    """
    state = createExplanationPromise(tool_name, args)
    return ExplanationResult(state.explanation, state.risk_level)


def ExplanationResult(explanation: str, risk_level: str = "medium") -> str:
    """Format an explanation result for terminal display.

    Args:
        explanation: The explanation text.
        risk_level: Risk level string.

    Returns:
        ANSI-formatted string.
    """
    color = getRiskColor(risk_level)
    label = getRiskLabel(risk_level)
    return f"  {color}{BOLD}{label}{RESET}: {explanation}"


def PermissionExplainerContent(
    tool_name: str,
    args: dict[str, Any] | None = None,
) -> str:
    """Full permission explainer content block.

    Args:
        tool_name: Tool name.
        args: Tool arguments.

    Returns:
        Formatted string with risk label and explanation.
    """
    state = createExplanationPromise(tool_name, args or {})
    return ExplanationResult(state.explanation, state.risk_level)
