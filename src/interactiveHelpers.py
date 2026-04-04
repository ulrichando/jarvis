"""Interactive helper utilities.

In the TypeScript version, these wrap React/Ink rendering for setup
dialogs and the main UI. The Python version provides the logic layer
without React/JSX dependencies.
"""

from __future__ import annotations

import sys
from typing import Any, Callable, Optional, TypeVar

T = TypeVar("T")


def complete_onboarding() -> None:
    """Mark onboarding as complete in global config."""
    # In full implementation, would save to global config
    pass


async def show_dialog(
    renderer: Callable[..., Any],
) -> Any:
    """Show a dialog and wait for result.

    In the TypeScript version, this renders React components in Ink.
    The Python version uses simpler terminal interaction.
    """
    # Stub - would use prompt_toolkit or similar in full implementation
    return None


async def show_setup_dialog(
    renderer: Callable[..., Any],
) -> Any:
    """Show a setup dialog wrapped in providers."""
    return await show_dialog(renderer)


async def exit_with_error(
    message: str,
    before_exit: Optional[Callable] = None,
) -> None:
    """Print an error message and exit."""
    print(f"\033[31m{message}\033[0m", file=sys.stderr)
    if before_exit:
        await before_exit()
    sys.exit(1)


async def exit_with_message(
    message: str,
    color: Optional[str] = None,
    exit_code: int = 1,
    before_exit: Optional[Callable] = None,
) -> None:
    """Print a message and exit."""
    if color == "error":
        print(f"\033[31m{message}\033[0m", file=sys.stderr)
    elif color:
        print(message, file=sys.stderr)
    else:
        print(message)
    if before_exit:
        await before_exit()
    sys.exit(exit_code)


async def render_and_run(element: Any) -> None:
    """Render the main UI and wait for it to exit.

    In the TypeScript version, this renders into an Ink root.
    The Python version would use the CLI/web shell directly.
    """
    pass


def get_render_context() -> Any:
    """Get the current render context."""
    return None


async def show_setup_screens() -> Any:
    """Show setup screens (onboarding, trust dialog, etc.)."""
    return None
