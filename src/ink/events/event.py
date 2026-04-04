"""Base event class with immediate propagation stop support."""


class Event:
    """Base event with stopImmediatePropagation support."""

    def __init__(self) -> None:
        self._did_stop_immediate_propagation: bool = False

    def did_stop_immediate_propagation(self) -> bool:
        return self._did_stop_immediate_propagation

    def stop_immediate_propagation(self) -> None:
        self._did_stop_immediate_propagation = True
