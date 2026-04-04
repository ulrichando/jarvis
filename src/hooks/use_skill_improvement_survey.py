"""Skill improvement survey management."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional


@dataclass
class SkillUpdate:
    field: str
    value: Any


@dataclass
class SkillImprovementSuggestion:
    skill_name: str
    updates: List[SkillUpdate]


class SkillImprovementSurvey:
    """Manages skill improvement suggestions and user responses.

    Equivalent to useSkillImprovementSurvey React hook.
    """

    def __init__(
        self,
        set_messages: Callable,
        get_app_state: Callable,
        set_app_state: Callable,
        apply_improvement: Optional[Callable] = None,
        log_event: Optional[Callable] = None,
    ):
        self._set_messages = set_messages
        self._get_app_state = get_app_state
        self._set_app_state = set_app_state
        self._apply_improvement = apply_improvement
        self._log_event = log_event
        self.is_open = False
        self._last_suggestion: Optional[SkillImprovementSuggestion] = None

    @property
    def suggestion(self) -> Optional[SkillImprovementSuggestion]:
        state = self._get_app_state()
        current = state.get("skill_improvement", {}).get("suggestion")
        if current:
            self._last_suggestion = current
        return self._last_suggestion

    def check_for_new_suggestion(self) -> None:
        """Check if a new suggestion has arrived and open the survey."""
        state = self._get_app_state()
        current = state.get("skill_improvement", {}).get("suggestion")
        if current and not self.is_open:
            self.is_open = True
            self._last_suggestion = current

    def handle_select(self, response: str) -> None:
        """Handle user response to the survey.

        Args:
            response: 'applied' or 'dismissed'
        """
        current = self._last_suggestion
        if not current:
            return

        applied = response != "dismissed"

        if self._log_event:
            self._log_event("skill_improvement_survey", {
                "event_type": "responded",
                "response": "applied" if applied else "dismissed",
                "skill_name": current.skill_name,
            })

        if applied and self._apply_improvement:
            self._apply_improvement(current.skill_name, current.updates)

        self.is_open = False
        state = self._get_app_state()
        state["skill_improvement"] = {"suggestion": None}
        self._set_app_state(state)
