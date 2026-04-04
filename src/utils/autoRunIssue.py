"""Auto-run issue notification logic (non-UI parts)."""

from __future__ import annotations

from typing import Literal

AutoRunIssueReason = Literal["feedback_survey_bad", "feedback_survey_good"]


def should_auto_run_issue(reason: AutoRunIssueReason) -> bool:
    """Determines if /issue should auto-run for Ant users."""
    # Only for Ant users - external builds always return False
    return False


def get_auto_run_command(reason: AutoRunIssueReason) -> str:
    """Returns the appropriate command to auto-run based on the reason."""
    return "/issue"


def get_auto_run_issue_reason_text(reason: AutoRunIssueReason) -> str:
    """Gets a human-readable description of why /issue is being auto-run."""
    if reason == "feedback_survey_bad":
        return 'You responded "Bad" to the feedback survey'
    elif reason == "feedback_survey_good":
        return 'You responded "Good" to the feedback survey'
    return "Unknown reason"
