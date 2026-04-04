"""Model migration notification."""

from __future__ import annotations

from typing import Callable, Optional


def check_model_migration(
    add_notification: Optional[Callable] = None,
    current_model: Optional[str] = None,
    recommended_model: Optional[str] = None,
) -> None:
    """Show notification when a model migration is recommended.

    Equivalent to useModelMigrationNotifications React hook.
    """
    if not add_notification or not current_model or not recommended_model:
        return
    if current_model != recommended_model:
        add_notification(
            key="model-migration",
            text=f"Consider upgrading from {current_model} to {recommended_model}",
            priority="low",
        )
