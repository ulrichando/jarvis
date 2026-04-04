"""Feedback component for terminal.

Collects user feedback on responses, with thumbs up/down,
error reporting, and issue submission.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Optional
import re
import os
import time
import json
import urllib.parse
import logging

CYAN = "\033[36m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"

GITHUB_URL_LIMIT = 8000  # Max URL length for GitHub issues

logger = logging.getLogger(__name__)


@dataclass
class Props:
    """Properties for the Feedback component."""
    session_id: str = ""
    transcript_path: str = ""


@dataclass
class FeedbackData:
    """Data collected from a feedback submission."""
    rating: int = 0  # -1 = thumbs down, 0 = neutral, 1 = thumbs up
    comment: str = ""
    session_id: str = ""
    timestamp: float = 0.0
    error_logs: list[str] = field(default_factory=list)
    env_info: dict[str, str] = field(default_factory=dict)


# Patterns for sensitive information redaction
_SENSITIVE_PATTERNS = [
    (re.compile(r"(?i)(api[_-]?key|token|secret|password|credential)[\s=:]+\S+"), r"\1=<REDACTED>"),
    (re.compile(r"sk-[a-zA-Z0-9]{20,}"), "<API_KEY_REDACTED>"),
    (re.compile(r"ghp_[a-zA-Z0-9]{36}"), "<GITHUB_TOKEN_REDACTED>"),
    (re.compile(r"gho_[a-zA-Z0-9]{36}"), "<GITHUB_TOKEN_REDACTED>"),
    (re.compile(r"xoxb-[a-zA-Z0-9-]+"), "<SLACK_TOKEN_REDACTED>"),
    (re.compile(r"(?i)bearer\s+\S+"), "Bearer <REDACTED>"),
    (re.compile(r"/home/[^/\s]+"), "/home/<USER>"),
    (re.compile(r"C:\\Users\\[^\\]+"), "C:\\Users\\<USER>"),
]


def redactSensitiveInfo(text: str) -> str:
    """Redact sensitive information from text.

    Removes API keys, tokens, passwords, and user paths.

    Args:
        text: Text that may contain sensitive data.

    Returns:
        Redacted text.
    """
    result = text
    for pattern, replacement in _SENSITIVE_PATTERNS:
        result = pattern.sub(replacement, result)
    return result


def getSanitizedErrorLogs(
    logs: list[str],
    max_entries: int = 20,
) -> list[str]:
    """Get sanitized error logs for feedback submission.

    Args:
        logs: Raw error log entries.
        max_entries: Maximum entries to include.

    Returns:
        List of redacted log entries.
    """
    sanitized = []
    for entry in logs[-max_entries:]:
        sanitized.append(redactSensitiveInfo(entry))
    return sanitized


def loadRawTranscriptJsonl(path: str) -> list[dict[str, Any]]:
    """Load a raw transcript from a JSONL file.

    Args:
        path: Path to the JSONL transcript file.

    Returns:
        List of message dicts.
    """
    messages = []
    try:
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        messages.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except (OSError, IOError):
        pass
    return messages


def loadEnvInfo() -> dict[str, str]:
    """Load environment information for feedback.

    Returns:
        Dict with OS, Python version, and other relevant info.
    """
    import platform
    return {
        "os": platform.system(),
        "os_version": platform.release(),
        "python": platform.python_version(),
        "arch": platform.machine(),
    }


def createFallbackTitle(session_id: str = "") -> str:
    """Create a fallback issue title when auto-generation fails.

    Args:
        session_id: Session identifier.

    Returns:
        Generic issue title.
    """
    date_str = time.strftime("%Y-%m-%d")
    if session_id:
        return f"Feedback: session {session_id[:8]} ({date_str})"
    return f"Feedback: {date_str}"


def generateTitle(comment: str, session_id: str = "") -> str:
    """Generate an issue title from a feedback comment.

    Args:
        comment: User's feedback comment.
        session_id: Session identifier.

    Returns:
        Generated title string.
    """
    if not comment:
        return createFallbackTitle(session_id)

    # Use first sentence or first 60 chars
    first_sentence = comment.split(".")[0].split("\n")[0].strip()
    if len(first_sentence) > 60:
        first_sentence = first_sentence[:57] + "..."

    if len(first_sentence) < 5:
        return createFallbackTitle(session_id)

    return first_sentence


def createGitHubIssueUrl(
    title: str,
    body: str,
    repo: str = "",
    labels: list[str] | None = None,
) -> str:
    """Create a GitHub issue URL with pre-filled content.

    Args:
        title: Issue title.
        body: Issue body.
        repo: Repository URL (e.g. 'owner/repo').
        labels: Issue labels.

    Returns:
        Full GitHub new issue URL, or empty string if no repo.
    """
    if not repo:
        return ""

    params = {"title": title, "body": body}
    if labels:
        params["labels"] = ",".join(labels)

    query = urllib.parse.urlencode(params)
    url = f"https://github.com/{repo}/issues/new?{query}"

    if len(url) > GITHUB_URL_LIMIT:
        # Truncate body to fit
        max_body = GITHUB_URL_LIMIT - len(url) + len(body) - 100
        if max_body > 0:
            params["body"] = body[:max_body] + "\n\n(truncated)"
            query = urllib.parse.urlencode(params)
            url = f"https://github.com/{repo}/issues/new?{query}"

    return url


def sanitizeAndLogError(error: str, context: str = "") -> str:
    """Sanitize an error message and log it.

    Args:
        error: Raw error message.
        context: Context information.

    Returns:
        Sanitized error string.
    """
    sanitized = redactSensitiveInfo(error)
    if context:
        logger.error("Feedback error [%s]: %s", context, sanitized)
    else:
        logger.error("Feedback error: %s", sanitized)
    return sanitized


def submitFeedback(data: FeedbackData) -> bool:
    """Submit feedback data (saves to local file).

    Args:
        data: Feedback data to submit.

    Returns:
        True if saved successfully.
    """
    feedback_dir = os.path.expanduser("~/.jarvis/feedback")
    os.makedirs(feedback_dir, exist_ok=True)

    filename = f"feedback_{int(data.timestamp or time.time())}.json"
    filepath = os.path.join(feedback_dir, filename)

    entry = {
        "rating": data.rating,
        "comment": redactSensitiveInfo(data.comment),
        "session_id": data.session_id,
        "timestamp": data.timestamp or time.time(),
        "env_info": data.env_info,
        "error_logs": getSanitizedErrorLogs(data.error_logs),
    }

    try:
        with open(filepath, "w") as f:
            json.dump(entry, f, indent=2)
        return True
    except OSError:
        return False


def Feedback(
    rating: int = 0,
    prompt_text: str = "",
) -> str:
    """Format a feedback prompt for terminal display.

    Args:
        rating: Current rating (-1, 0, 1).
        prompt_text: Optional prompt text.

    Returns:
        Formatted feedback prompt.
    """
    if prompt_text:
        header = prompt_text
    else:
        header = "How was this response?"

    thumbs_up = f"{GREEN}[+]{RESET}" if rating == 1 else f"{DIM}[+]{RESET}"
    thumbs_down = f"{RED}[-]{RESET}" if rating == -1 else f"{DIM}[-]{RESET}"

    return (
        f"\n{DIM}{header}{RESET}\n"
        f"  {thumbs_up} Good  {thumbs_down} Bad  {DIM}[s]{RESET} Skip\n"
    )
