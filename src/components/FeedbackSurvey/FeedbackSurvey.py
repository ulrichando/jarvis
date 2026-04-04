"""Feedback survey component for terminal.

Presents survey questions and collects ratings.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Optional

CYAN = "\033[36m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"


@dataclass
class Props:
    """Properties for FeedbackSurvey."""
    questions: list[str] = field(default_factory=list)
    current_index: int = 0
    answers: dict[int, int] = field(default_factory=dict)


@dataclass
class ThanksProps:
    """Properties for the thank-you message."""
    total_questions: int = 0
    answers: dict[int, int] = field(default_factory=dict)


# Default survey questions
DEFAULT_QUESTIONS = [
    "Was the response helpful?",
    "Was the response accurate?",
    "Did the response address your request?",
]


def FeedbackSurvey(
    questions: list[str] | None = None,
    current_index: int = 0,
    answers: dict[int, int] | None = None,
) -> str:
    """Format a feedback survey for terminal display.

    Displays the current question with a 1-5 rating scale.

    Args:
        questions: List of survey questions.
        current_index: Index of the current question.
        answers: Dict mapping question index to rating (1-5).

    Returns:
        Formatted survey display string.
    """
    questions = questions or DEFAULT_QUESTIONS
    answers = answers or {}

    if current_index >= len(questions):
        return FeedbackSurveyThanks(len(questions), answers)

    question = questions[current_index]
    progress = f"{current_index + 1}/{len(questions)}"

    lines = [
        "",
        f"{BOLD}{CYAN}--- Feedback ({progress}) ---{RESET}",
        f"  {question}",
        "",
    ]

    # Rating scale 1-5
    scale_parts = []
    for i in range(1, 6):
        if answers.get(current_index) == i:
            scale_parts.append(f"{GREEN}{BOLD}[{i}]{RESET}")
        else:
            scale_parts.append(f"{DIM}[{i}]{RESET}")

    lines.append(f"  {' '.join(scale_parts)}")
    lines.append(f"  {DIM}1=Poor  3=OK  5=Excellent{RESET}")
    lines.append("")
    lines.append(f"  {DIM}[1-5] rate  [s] skip  [q] quit survey{RESET}")
    lines.append(f"{BOLD}{CYAN}------------------------{RESET}")
    lines.append("")

    return "\n".join(lines)


def FeedbackSurveyThanks(
    total_questions: int = 0,
    answers: dict[int, int] | None = None,
) -> str:
    """Format the thank-you message after survey completion.

    Args:
        total_questions: Total number of questions.
        answers: Collected answers.

    Returns:
        Formatted thank-you string.
    """
    answers = answers or {}
    answered = len(answers)

    if answered == 0:
        return f"\n{DIM}Survey skipped.{RESET}\n"

    avg = sum(answers.values()) / answered if answered > 0 else 0

    lines = [
        "",
        f"{GREEN}Thank you for your feedback!{RESET}",
        f"{DIM}Answered {answered}/{total_questions} questions (avg: {avg:.1f}/5){RESET}",
        "",
    ]
    return "\n".join(lines)
