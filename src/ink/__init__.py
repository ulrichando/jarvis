"""Ink - Terminal UI framework (Python port).

Provides Python equivalents for the React/Ink terminal UI rendering.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional


@dataclass
class RenderOptions:
    """Options for terminal rendering."""
    stdout: Any = None
    stderr: Any = None
    stdin: Any = None
    debug: bool = False
    exit_on_ctrl_c: bool = True
    patch_console: bool = True


@dataclass
class Instance:
    """A rendering instance."""
    cleanup: Optional[Callable[[], None]] = None
    unmount: Optional[Callable[[], None]] = None
    rerender: Optional[Callable[[Any], None]] = None
    wait_until_exit: Optional[Callable[[], Any]] = None


@dataclass
class Root:
    """A rendering root."""
    render: Optional[Callable[[Any], None]] = None
    unmount: Optional[Callable[[], None]] = None


async def render(node: Any, options: Optional[RenderOptions] = None) -> Instance:
    """Render a node to the terminal."""
    # Stub - Python rendering uses different mechanisms
    return Instance()


async def create_root(options: Optional[RenderOptions] = None) -> Root:
    """Create a rendering root."""
    # Stub - Python rendering uses different mechanisms
    return Root()


# Color utilities
def color(name: str) -> str:
    """Get a named color value."""
    # Simplified color mapping
    colors = {
        "error": "\033[31m",
        "warning": "\033[33m",
        "success": "\033[32m",
        "info": "\033[36m",
        "dim": "\033[2m",
        "reset": "\033[0m",
    }
    return colors.get(name, "")
