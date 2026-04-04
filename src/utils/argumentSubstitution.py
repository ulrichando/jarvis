"""
Utility for substituting $ARGUMENTS placeholders in skill/command prompts.

Supports:
- $ARGUMENTS - replaced with the full arguments string
- $ARGUMENTS[0], $ARGUMENTS[1], etc. - replaced with individual indexed arguments
- $0, $1, etc. - shorthand for $ARGUMENTS[0], $ARGUMENTS[1]
- Named arguments (e.g., $foo, $bar) - when argument names are defined in frontmatter
"""

from __future__ import annotations

import re
import shlex
from typing import Optional, Union


def parse_arguments(args: str) -> list[str]:
    """
    Parse an arguments string into an array of individual arguments.
    Uses shlex for proper shell argument parsing including quoted strings.

    Examples:
        "foo bar baz" => ["foo", "bar", "baz"]
        'foo "hello world" baz' => ["foo", "hello world", "baz"]
    """
    if not args or not args.strip():
        return []

    try:
        return shlex.split(args)
    except ValueError:
        # Fall back to simple whitespace split if parsing fails
        return [s for s in args.split() if s]


def parse_argument_names(argument_names: Union[str, list[str], None]) -> list[str]:
    """
    Parse argument names from the frontmatter 'arguments' field.
    Accepts either a space-separated string or a list of strings.
    """
    if argument_names is None:
        return []

    def is_valid_name(name: str) -> bool:
        return isinstance(name, str) and name.strip() != "" and not name.isdigit()

    if isinstance(argument_names, list):
        return [n for n in argument_names if is_valid_name(n)]
    if isinstance(argument_names, str):
        return [n for n in argument_names.split() if is_valid_name(n)]
    return []


def generate_progressive_argument_hint(
    arg_names: list[str], typed_args: list[str]
) -> Optional[str]:
    """
    Generate argument hint showing remaining unfilled args.

    Args:
        arg_names: Array of argument names from frontmatter.
        typed_args: Arguments the user has typed so far.

    Returns:
        Hint string like "[arg2] [arg3]" or None if all filled.
    """
    remaining = arg_names[len(typed_args) :]
    if not remaining:
        return None
    return " ".join(f"[{name}]" for name in remaining)


def substitute_arguments(
    content: str,
    args: Optional[str],
    append_if_no_placeholder: bool = True,
    argument_names: Optional[list[str]] = None,
) -> str:
    """
    Substitute $ARGUMENTS placeholders in content with actual argument values.

    Args:
        content: The content containing placeholders.
        args: The raw arguments string (may be None).
        append_if_no_placeholder: If True and no placeholders are found,
            appends "ARGUMENTS: {args}" to content.
        argument_names: Optional list of named arguments that map to indexed positions.

    Returns:
        The content with placeholders substituted.
    """
    if args is None:
        return content

    if argument_names is None:
        argument_names = []

    parsed_args = parse_arguments(args)
    original_content = content

    # Replace named arguments (e.g., $foo, $bar) with their values
    for i, name in enumerate(argument_names):
        if not name:
            continue
        pattern = rf"\${re.escape(name)}(?![\[\w])"
        replacement = parsed_args[i] if i < len(parsed_args) else ""
        content = re.sub(pattern, replacement, content)

    # Replace indexed arguments ($ARGUMENTS[0], $ARGUMENTS[1], etc.)
    def replace_indexed(match: re.Match[str]) -> str:
        index = int(match.group(1))
        return parsed_args[index] if index < len(parsed_args) else ""

    content = re.sub(r"\$ARGUMENTS\[(\d+)\]", replace_indexed, content)

    # Replace shorthand indexed arguments ($0, $1, etc.)
    def replace_shorthand(match: re.Match[str]) -> str:
        index = int(match.group(1))
        return parsed_args[index] if index < len(parsed_args) else ""

    content = re.sub(r"\$(\d+)(?!\w)", replace_shorthand, content)

    # Replace $ARGUMENTS with the full arguments string
    content = content.replace("$ARGUMENTS", args)

    # If no placeholders were found and append_if_no_placeholder is True, append
    if content == original_content and append_if_no_placeholder and args:
        content = content + f"\n\nARGUMENTS: {args}"

    return content
