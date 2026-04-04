"""Swarm (team/teammate) initialization."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional


class SwarmInitializer:
    """Initializes swarm features: teammate hooks and context.

    Handles both fresh spawns and resumed teammate sessions.

    Equivalent to useSwarmInitialization React hook.
    """

    def __init__(
        self,
        set_app_state: Callable,
        initial_messages: Optional[List[Any]] = None,
        enabled: bool = True,
        is_swarms_enabled: Callable[[], bool] = lambda: False,
        initialize_from_session: Optional[Callable] = None,
        initialize_hooks: Optional[Callable] = None,
        get_dynamic_context: Optional[Callable] = None,
        read_team_file: Optional[Callable] = None,
    ):
        self._set_app_state = set_app_state
        self._initial_messages = initial_messages
        self._enabled = enabled
        self._is_swarms_enabled = is_swarms_enabled
        self._initialize_from_session = initialize_from_session
        self._initialize_hooks = initialize_hooks
        self._get_dynamic_context = get_dynamic_context
        self._read_team_file = read_team_file

    def initialize(self) -> None:
        """Initialize swarm features if enabled."""
        if not self._enabled or not self._is_swarms_enabled():
            return

        # Check if this is a resumed session
        first_msg = (
            self._initial_messages[0] if self._initial_messages else None
        )
        team_name = getattr(first_msg, "team_name", None) if first_msg else None
        agent_name = getattr(first_msg, "agent_name", None) if first_msg else None

        if team_name and agent_name and self._initialize_from_session:
            self._initialize_from_session(team_name, agent_name)
        elif self._get_dynamic_context:
            context = self._get_dynamic_context()
            if context and self._initialize_hooks:
                self._initialize_hooks(context)
