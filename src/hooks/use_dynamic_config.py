"""Dynamic configuration value management."""

from __future__ import annotations

from typing import Any, Callable, Optional, TypeVar

T = TypeVar("T")


class DynamicConfig:
    """Manages dynamic configuration values that may be fetched asynchronously.

    Equivalent to useDynamicConfig React hook.
    """

    def __init__(
        self,
        config_name: str,
        default_value: Any,
        fetch_fn: Optional[Callable] = None,
    ):
        self.config_name = config_name
        self.value = default_value
        self._default = default_value
        self._fetch_fn = fetch_fn

    async def fetch(self) -> None:
        """Fetch the dynamic config value."""
        if self._fetch_fn:
            try:
                self.value = await self._fetch_fn(self.config_name, self._default)
            except Exception:
                self.value = self._default

    def reset(self) -> None:
        """Reset to default value."""
        self.value = self._default
