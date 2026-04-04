"""Bash command parser using tree-sitter or fallback."""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from typing import Optional

from .bashParser import TsNode

Node = TsNode

MAX_COMMAND_LENGTH = 10000
DECLARATION_COMMANDS = {"export", "declare", "typeset", "readonly", "local", "unset", "unsetenv"}
ARGUMENT_TYPES = {"word", "string", "raw_string", "number"}
COMMAND_TYPES = {"command", "declaration_command"}

PARSE_ABORTED = "parse_aborted"


@dataclass
class ParsedCommandData:
    root_node: Node
    env_vars: list[str] = field(default_factory=list)
    command_node: Optional[Node] = None
    original_command: str = ""


async def ensure_initialized() -> None:
    """Ensure parser is initialized."""
    pass


async def parse_command(command: str) -> Optional[ParsedCommandData]:
    """Parse a bash command string into an AST."""
    if not command or len(command) > MAX_COMMAND_LENGTH:
        return None

    from .bashParser import parse_source

    root = parse_source(command)
    if root is None:
        return None

    cmd_node = None
    if root.children:
        cmd_node = root.children[0]

    return ParsedCommandData(
        root_node=root,
        command_node=cmd_node,
        original_command=command,
    )


def parse_command_raw(command: str) -> Optional[Node]:
    """Parse a command and return the raw root node."""
    from .bashParser import parse_source
    return parse_source(command)


def extract_command_arguments(node: Node) -> list[str]:
    """Extract command arguments from a parsed command node."""
    try:
        return shlex.split(node.text)
    except ValueError:
        return [node.text]
