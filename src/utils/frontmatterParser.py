"""
Frontmatter parser for markdown files.

Extracts and parses YAML frontmatter between --- delimiters.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Literal, Optional, Union

logger = logging.getLogger(__name__)

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]


@dataclass
class FrontmatterData:
    """Parsed frontmatter key-value pairs."""

    allowed_tools: Optional[Union[str, list[str]]] = None
    description: Optional[str] = None
    type: Optional[str] = None
    argument_hint: Optional[str] = None
    when_to_use: Optional[str] = None
    version: Optional[str] = None
    hide_from_slash_command_tool: Optional[str] = None
    model: Optional[str] = None
    skills: Optional[str] = None
    user_invocable: Optional[str] = None
    hooks: Optional[dict] = None
    effort: Optional[str] = None
    context: Optional[Literal["inline", "fork"]] = None
    agent: Optional[str] = None
    paths: Optional[Union[str, list[str]]] = None
    shell: Optional[str] = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedMarkdown:
    """Result of parsing a markdown file with frontmatter."""

    frontmatter: dict[str, Any]
    content: str


# Characters that require quoting in YAML values
YAML_SPECIAL_CHARS = re.compile(r"[{}\[\]*&#!|>%@`]|: ")

FRONTMATTER_REGEX = re.compile(r"^---\s*\n([\s\S]*?)---\s*\n?")


def _quote_problematic_values(frontmatter_text: str) -> str:
    """Pre-process frontmatter to quote values with special YAML characters."""
    lines = frontmatter_text.split("\n")
    result: list[str] = []

    for line in lines:
        match = re.match(r"^([a-zA-Z_-]+):\s+(.+)$", line)
        if match:
            key, value = match.group(1), match.group(2)
            if not key or not value:
                result.append(line)
                continue

            # Skip if already quoted
            if (value.startswith('"') and value.endswith('"')) or (
                value.startswith("'") and value.endswith("'")
            ):
                result.append(line)
                continue

            # Quote if contains special YAML characters
            if YAML_SPECIAL_CHARS.search(value):
                escaped = value.replace("\\", "\\\\").replace('"', '\\"')
                result.append(f'{key}: "{escaped}"')
                continue

        result.append(line)

    return "\n".join(result)


def parse_frontmatter(
    markdown: str, source_path: Optional[str] = None
) -> ParsedMarkdown:
    """
    Parse markdown content to extract frontmatter and content.

    Args:
        markdown: The raw markdown content.
        source_path: Optional path for error messages.

    Returns:
        ParsedMarkdown with frontmatter dict and content string.
    """
    match = FRONTMATTER_REGEX.match(markdown)

    if not match:
        return ParsedMarkdown(frontmatter={}, content=markdown)

    frontmatter_text = match.group(1) or ""
    content = markdown[match.end() :]

    frontmatter: dict[str, Any] = {}

    if yaml is None:
        logger.warning("PyYAML not installed; frontmatter parsing disabled")
        return ParsedMarkdown(frontmatter=frontmatter, content=content)

    try:
        parsed = yaml.safe_load(frontmatter_text)
        if isinstance(parsed, dict):
            frontmatter = parsed
    except yaml.YAMLError:
        # YAML parsing failed - try again after quoting problematic values
        try:
            quoted_text = _quote_problematic_values(frontmatter_text)
            parsed = yaml.safe_load(quoted_text)
            if isinstance(parsed, dict):
                frontmatter = parsed
        except yaml.YAMLError as retry_error:
            location = f" in {source_path}" if source_path else ""
            logger.warning(
                f"Failed to parse YAML frontmatter{location}: {retry_error}"
            )

    return ParsedMarkdown(frontmatter=frontmatter, content=content)


def split_path_in_frontmatter(input_val: Union[str, list[str]]) -> list[str]:
    """
    Split a comma-separated string and expand brace patterns.
    Commas inside braces are not treated as separators.
    Also accepts a list of strings.

    Examples:
        split_path_in_frontmatter("a, b") -> ["a", "b"]
        split_path_in_frontmatter("a, src/*.{ts,tsx}") -> ["a", "src/*.ts", "src/*.tsx"]
    """
    if isinstance(input_val, list):
        result: list[str] = []
        for item in input_val:
            result.extend(split_path_in_frontmatter(item))
        return result

    if not isinstance(input_val, str):
        return []

    # Split by comma while respecting braces
    parts: list[str] = []
    current = ""
    brace_depth = 0

    for char in input_val:
        if char == "{":
            brace_depth += 1
            current += char
        elif char == "}":
            brace_depth -= 1
            current += char
        elif char == "," and brace_depth == 0:
            trimmed = current.strip()
            if trimmed:
                parts.append(trimmed)
            current = ""
        else:
            current += char

    trimmed = current.strip()
    if trimmed:
        parts.append(trimmed)

    # Expand brace patterns
    result = []
    for pattern in parts:
        if pattern:
            result.extend(_expand_braces(pattern))
    return result


def _expand_braces(pattern: str) -> list[str]:
    """
    Expand brace patterns in a glob string.

    Examples:
        _expand_braces("src/*.{ts,tsx}") -> ["src/*.ts", "src/*.tsx"]
        _expand_braces("{a,b}/{c,d}") -> ["a/c", "a/d", "b/c", "b/d"]
    """
    brace_match = re.match(r"^([^{]*)\{([^}]+)\}(.*)$", pattern)

    if not brace_match:
        return [pattern]

    prefix = brace_match.group(1) or ""
    alternatives = brace_match.group(2) or ""
    suffix = brace_match.group(3) or ""

    parts = [alt.strip() for alt in alternatives.split(",")]

    expanded: list[str] = []
    for part in parts:
        combined = prefix + part + suffix
        expanded.extend(_expand_braces(combined))

    return expanded


def parse_positive_int_from_frontmatter(value: Any) -> Optional[int]:
    """
    Parse a positive integer value from frontmatter.
    Handles both number and string representations.
    """
    if value is None:
        return None

    if isinstance(value, int) and value > 0:
        return value

    try:
        parsed = int(str(value))
        if parsed > 0:
            return parsed
    except (ValueError, TypeError):
        pass

    return None


def coerce_description_to_string(
    value: Any,
    component_name: Optional[str] = None,
    plugin_name: Optional[str] = None,
) -> Optional[str]:
    """
    Validate and coerce a description value from frontmatter.

    Strings are returned as-is (trimmed). Primitive values are coerced to
    strings. Non-scalar values (lists, dicts) are invalid and logged.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, (int, float, bool)):
        return str(value)

    source = (
        f"{plugin_name}:{component_name}"
        if plugin_name
        else (component_name or "unknown")
    )
    logger.warning(f"Description invalid for {source} - omitting")
    return None


def parse_boolean_frontmatter(value: Any) -> bool:
    """Parse a boolean frontmatter value. Only true for literal True or 'true'."""
    return value is True or value == "true"


FrontmatterShell = Literal["bash", "powershell"]
FRONTMATTER_SHELLS: tuple[FrontmatterShell, ...] = ("bash", "powershell")


def parse_shell_frontmatter(
    value: Any, source: str
) -> Optional[FrontmatterShell]:
    """
    Parse and validate the shell frontmatter field.

    Returns None for absent/null/empty (caller defaults to bash).
    Logs a warning for unrecognized values.
    """
    if value is None:
        return None

    normalized = str(value).strip().lower()
    if not normalized:
        return None

    if normalized in FRONTMATTER_SHELLS:
        return normalized  # type: ignore[return-value]

    logger.warning(
        f"Frontmatter 'shell: {value}' in {source} is not recognized. "
        f"Valid values: {', '.join(FRONTMATTER_SHELLS)}. Falling back to bash."
    )
    return None
