"""ErrorOverview component - displays error information."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ErrorOverviewProps:
    error: Exception | None = None


class ErrorOverview:
    """Displays an error with stack trace information."""

    def __init__(self, props: ErrorOverviewProps | None = None) -> None:
        self.props = props or ErrorOverviewProps()

    def get_error_text(self) -> str:
        if not self.props.error:
            return ""
        error = self.props.error
        return f"{type(error).__name__}: {error}"
