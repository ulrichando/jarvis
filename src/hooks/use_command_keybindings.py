"""Register keybinding handlers for command bindings."""

from __future__ import annotations

from typing import Callable, Dict, Optional, Set


class CommandKeybindingHandlers:
    """Registers keybinding handlers for 'command:*' actions.

    When triggered, each handler submits the corresponding slash command
    (e.g., 'command:commit' submits '/commit').

    Equivalent to CommandKeybindingHandlers React component.
    """

    def __init__(
        self,
        on_submit: Callable[[str], None],
        get_bindings: Optional[Callable] = None,
        is_active: bool = True,
    ):
        self._on_submit = on_submit
        self._get_bindings = get_bindings
        self._is_active = is_active
        self._handlers: Dict[str, Callable] = {}
        self._build_handlers()

    def _build_handlers(self) -> None:
        if not self._get_bindings:
            return

        bindings = self._get_bindings()
        actions: Set[str] = set()

        for binding in bindings:
            action = binding.get("action", "")
            if action.startswith("command:"):
                actions.add(action)

        for action in actions:
            command_name = action[len("command:"):]
            self._handlers[action] = lambda cn=command_name: self._on_submit(f"/{cn}")

    def handle_action(self, action: str) -> bool:
        """Handle an action. Returns True if handled."""
        if not self._is_active or action not in self._handlers:
            return False
        self._handlers[action]()
        return True
