"""Prompt suggestion management for typeahead/autocomplete."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class PromptSuggestionState:
    text: Optional[str] = None
    prompt_id: Optional[str] = None
    shown_at: float = 0
    accepted_at: float = 0
    generation_request_id: Optional[str] = None


class PromptSuggestion:
    """Manages prompt suggestion state and telemetry.

    Equivalent to usePromptSuggestion React hook.
    """

    def __init__(
        self,
        get_state: Callable[[], PromptSuggestionState],
        set_state: Callable[[PromptSuggestionState], None],
        log_event: Optional[Callable] = None,
    ):
        self._get_state = get_state
        self._set_state = set_state
        self._log_event = log_event
        self._first_keystroke_at: float = 0
        self._was_focused_when_shown: bool = True
        self._prev_shown_at: float = 0

    def get_suggestion(
        self, input_value: str, is_assistant_responding: bool
    ) -> Optional[str]:
        """Get the current suggestion text, if applicable."""
        state = self._get_state()
        if is_assistant_responding or len(input_value) > 0:
            return None
        return state.text

    @property
    def _is_valid(self) -> bool:
        state = self._get_state()
        return state.text is not None and state.shown_at > 0

    def mark_accepted(self) -> None:
        """Mark the current suggestion as accepted (Tab pressed)."""
        if not self._is_valid:
            return
        state = self._get_state()
        state.accepted_at = time.time() * 1000
        self._set_state(state)

    def mark_shown(self) -> None:
        """Mark the current suggestion as shown."""
        state = self._get_state()
        if state.shown_at != 0 or state.text is None:
            return
        state.shown_at = time.time() * 1000
        self._set_state(state)

    def record_first_keystroke(self, input_value: str) -> None:
        """Record when the user starts typing while suggestion is visible."""
        if len(input_value) > 0 and self._first_keystroke_at == 0 and self._is_valid:
            self._first_keystroke_at = time.time() * 1000

    def log_outcome_at_submission(
        self,
        final_input: str,
        skip_reset: bool = False,
    ) -> None:
        """Log telemetry about suggestion outcome at submission time."""
        if not self._is_valid:
            return

        state = self._get_state()
        tab_was_pressed = state.accepted_at > state.shown_at
        was_accepted = tab_was_pressed or final_input == state.text
        now = time.time() * 1000

        if self._log_event:
            event_data = {
                "source": "cli",
                "outcome": "accepted" if was_accepted else "ignored",
                "prompt_id": state.prompt_id,
            }
            if was_accepted:
                event_data["accept_method"] = "tab" if tab_was_pressed else "enter"
                event_data["time_to_accept_ms"] = (state.accepted_at or now) - state.shown_at
            else:
                event_data["time_to_ignore_ms"] = now - state.shown_at

            self._log_event("prompt_suggestion", event_data)

        if not skip_reset:
            self.reset()

    def reset(self) -> None:
        """Reset suggestion state."""
        self._set_state(PromptSuggestionState())
        self._first_keystroke_at = 0
