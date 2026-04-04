"""Singleton manager for cloud-provider authentication status.

Originally AWS-only; now used by all cloud auth refresh flows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class AwsAuthStatus:
    is_authenticating: bool = False
    output: list[str] = field(default_factory=list)
    error: Optional[str] = None


class AwsAuthStatusManager:
    """Singleton manager for authentication status."""

    _instance: Optional[AwsAuthStatusManager] = None
    _subscribers: list[Callable[[AwsAuthStatus], None]]

    def __init__(self) -> None:
        self._status = AwsAuthStatus()
        self._subscribers = []

    @classmethod
    def get_instance(cls) -> AwsAuthStatusManager:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def get_status(self) -> AwsAuthStatus:
        return AwsAuthStatus(
            is_authenticating=self._status.is_authenticating,
            output=list(self._status.output),
            error=self._status.error,
        )

    def start_authentication(self) -> None:
        self._status = AwsAuthStatus(is_authenticating=True)
        self._emit()

    def add_output(self, line: str) -> None:
        self._status.output.append(line)
        self._emit()

    def set_error(self, error: str) -> None:
        self._status.error = error
        self._emit()

    def end_authentication(self, success: bool) -> None:
        if success:
            self._status = AwsAuthStatus()
        else:
            self._status.is_authenticating = False
        self._emit()

    def subscribe(self, callback: Callable[[AwsAuthStatus], None]) -> Callable[[], None]:
        self._subscribers.append(callback)

        def unsubscribe() -> None:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

        return unsubscribe

    def _emit(self) -> None:
        status = self.get_status()
        for cb in self._subscribers:
            cb(status)

    @classmethod
    def reset(cls) -> None:
        if cls._instance is not None:
            cls._instance._subscribers.clear()
            cls._instance = None
