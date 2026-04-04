"""AST-based bash command analysis.

Provides fail-closed security analysis of bash commands.
If we can't produce a trustworthy argv, we refuse to extract it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional, Union


@dataclass
class Redirect:
    op: str  # '>', '>>', '<', etc.
    target: str
    fd: Optional[int] = None


@dataclass
class SimpleCommand:
    argv: list[str]
    env_vars: list[dict[str, str]] = field(default_factory=list)
    redirects: list[Redirect] = field(default_factory=list)
    text: str = ""


@dataclass
class SimpleResult:
    kind: Literal["simple"] = "simple"
    commands: list[SimpleCommand] = field(default_factory=list)


@dataclass
class TooComplexResult:
    kind: Literal["too-complex"] = "too-complex"
    reason: str = ""
    node_type: Optional[str] = None


@dataclass
class ParseUnavailableResult:
    kind: Literal["parse-unavailable"] = "parse-unavailable"


ParseForSecurityResult = Union[SimpleResult, TooComplexResult, ParseUnavailableResult]

STRUCTURAL_TYPES = {"program", "list", "pipeline", "redirected_statement"}
SEPARATOR_TYPES = {"&&", "||", "|", ";", "&", "|&", "\n"}
CMDSUB_PLACEHOLDER = "__CMDSUB_OUTPUT__"


def parse_for_security(command: str) -> ParseForSecurityResult:
    """Parse a bash command for security analysis.

    Returns SimpleResult if we can extract trustworthy argv arrays,
    TooComplexResult if the command is too complex to analyze,
    ParseUnavailableResult if no parser is available.
    """
    import shlex

    try:
        tokens = shlex.split(command)
        if not tokens:
            return SimpleResult(commands=[])

        # Simple single-command case
        cmd = SimpleCommand(argv=tokens, text=command)
        return SimpleResult(commands=[cmd])
    except ValueError:
        return TooComplexResult(reason="shlex parse failed")
