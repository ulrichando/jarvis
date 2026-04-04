"""
Converted from OffscreenFreeze.tsx
Business logic extracted from TypeScript/TSX source.
"""

from dataclasses import dataclass, field
from typing import Any, Literal, Callable, Awaitable
import re


@dataclass
class Props:
    children: Any


# Ref: cached = children

def OffscreenFreeze(children):
    """React component OffscreenFreeze - UI rendering logic omitted."""
    # React Compiler: reading cached.current in the return is the entire
    # freeze mechanism — memoizing this component would defeat it. Opt out.
    'use no memo'

    inVirtualList = useContext(InVirtualListContext)
    const [ref, {
        isVisible
    }] = useTerminalViewport()
    cached = useRef(children)
    # Virtual list has no terminal scrollback — the ScrollBox clips inside the
    # viewport, so there's nothing to freeze. Freezing there also blocks
    # click-to-expand since useTerminalViewport's visibility calc can disagree
    # with the ScrollBox's virtual scroll position.
    if isVisible or inVirtualList:
        cached.current = children
    return None  # JSX rendering omitted
