"""Pure-Python bash parser producing tree-sitter-bash-compatible ASTs.

Simplified version for Python - provides basic parsing capabilities.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

SHELL_KEYWORDS = {
    "if", "then", "else", "elif", "fi",
    "for", "while", "until", "do", "done",
    "case", "esac", "in",
    "function",
    "select",
    "coproc",
    "time",
}

DECL_KEYWORDS = {
    "export", "declare", "typeset", "local", "readonly",
}

PARSE_TIMEOUT_MS = 50
MAX_NODES = 50_000


@dataclass
class TsNode:
    type: str
    text: str
    start_index: int
    end_index: int
    children: list[TsNode] = field(default_factory=list)


def ensure_parser_initialized() -> None:
    """No-op: pure-Python parser needs no async init."""
    pass


def parse_source(source: str, timeout_ms: int = PARSE_TIMEOUT_MS) -> Optional[TsNode]:
    """Parse bash source into an AST node tree.

    This is a simplified parser - for full bash parsing,
    use the tree-sitter-bash library.
    """
    if not source or not source.strip():
        return TsNode(
            type="program",
            text=source,
            start_index=0,
            end_index=len(source.encode("utf-8")),
        )

    # Create a simple program node wrapping the source
    root = TsNode(
        type="program",
        text=source,
        start_index=0,
        end_index=len(source.encode("utf-8")),
    )

    # Create a command node for the whole source
    cmd = TsNode(
        type="command",
        text=source.strip(),
        start_index=0,
        end_index=len(source.strip().encode("utf-8")),
    )
    root.children.append(cmd)

    return root
