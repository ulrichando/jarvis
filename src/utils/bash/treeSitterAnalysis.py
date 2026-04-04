"""Tree-sitter AST analysis utilities for bash command security validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class QuoteContext:
    with_double_quotes: str = ""
    fully_unquoted: str = ""
    unquoted_keep_quote_chars: str = ""


@dataclass
class CompoundStructure:
    has_compound_operators: bool = False
    has_pipeline: bool = False
    has_subshell: bool = False
    has_command_group: bool = False
    operators: list[str] = field(default_factory=list)
    segments: list[str] = field(default_factory=list)


@dataclass
class DangerousPatterns:
    has_command_substitution: bool = False
    has_process_substitution: bool = False
    has_parameter_expansion: bool = False


@dataclass
class TreeSitterAnalysis:
    quote_context: QuoteContext = field(default_factory=QuoteContext)
    compound_structure: CompoundStructure = field(default_factory=CompoundStructure)
    dangerous_patterns: DangerousPatterns = field(default_factory=DangerousPatterns)


def analyze_command(root_node: any, command: str) -> TreeSitterAnalysis:
    """Analyze a parsed bash command for security-relevant patterns."""
    analysis = TreeSitterAnalysis()
    analysis.quote_context.with_double_quotes = command
    analysis.quote_context.fully_unquoted = command
    analysis.quote_context.unquoted_keep_quote_chars = command

    # Check for dangerous patterns in the raw command
    if "$(" in command or "`" in command:
        analysis.dangerous_patterns.has_command_substitution = True
    if "<(" in command or ">(" in command:
        analysis.dangerous_patterns.has_process_substitution = True
    if "${" in command:
        analysis.dangerous_patterns.has_parameter_expansion = True

    # Check for compound operators
    for op in ("&&", "||", ";"):
        if op in command:
            analysis.compound_structure.has_compound_operators = True
            analysis.compound_structure.operators.append(op)

    if "|" in command and "||" not in command:
        analysis.compound_structure.has_pipeline = True

    return analysis
