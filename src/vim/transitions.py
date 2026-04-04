"""Vim mode transitions."""

from __future__ import annotations

from .types import (
    InsertState,
    NormalState,
    IdleCommand,
    VimState,
)


def enter_insert(state: VimState) -> InsertState:
    """Transition to INSERT mode."""
    return InsertState(inserted_text="")


def enter_normal(state: VimState) -> NormalState:
    """Transition to NORMAL mode."""
    return NormalState(command=IdleCommand())


def enter_normal_from_insert(state: InsertState) -> NormalState:
    """Transition from INSERT to NORMAL mode, preserving inserted text."""
    return NormalState(command=IdleCommand())


def reset_command(state: NormalState) -> NormalState:
    """Reset the command state to idle."""
    return NormalState(command=IdleCommand())
