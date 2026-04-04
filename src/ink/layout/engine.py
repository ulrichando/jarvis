"""Layout engine factory."""

from .node import LayoutNode
from .yoga import create_yoga_layout_node


def create_layout_node() -> LayoutNode:
    """Create a new layout node."""
    return create_yoga_layout_node()
