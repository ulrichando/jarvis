"""MCP elicitation dialog for terminal.

Presents input forms when an MCP tool needs additional information from the user.
Supports text input, selection, multi-select, date, and URL fields.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Optional
from datetime import datetime

CYAN = "\033[36m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"

RESOLVING_SPINNER_CHARS = [".", "..", "..."]
LINES_PER_FIELD = 3
DIALOG_OVERHEAD = 6  # Header, footer, padding


@dataclass
class Props:
    """Properties for ElicitationDialog."""
    title: str = ""
    fields: list[dict[str, Any]] = field(default_factory=list)
    server_name: str = ""


def resetTypeahead() -> str:
    """Reset typeahead state indicator.

    Returns:
        Empty string (state reset).
    """
    return ""


def ResolvingSpinner(frame: int = 0) -> str:
    """Format a resolving/loading spinner.

    Args:
        frame: Animation frame index.

    Returns:
        Spinner text.
    """
    char = RESOLVING_SPINNER_CHARS[frame % len(RESOLVING_SPINNER_CHARS)]
    return f"{DIM}{char}{RESET}"


def formatDateDisplay(date_str: str) -> str:
    """Format a date string for display.

    Args:
        date_str: ISO date string or similar.

    Returns:
        Human-readable date string.
    """
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, AttributeError):
        return date_str


def validateRequired(value: Any, field_name: str = "field") -> Optional[str]:
    """Validate that a required field has a value.

    Args:
        value: The field value.
        field_name: Name of the field for error messages.

    Returns:
        Error message if invalid, None if valid.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return f"{field_name} is required"
    return None


def validateMultiSelect(
    selected: list[str],
    min_items: int = 0,
    max_items: int = 0,
) -> Optional[str]:
    """Validate a multi-select field.

    Args:
        selected: List of selected values.
        min_items: Minimum required selections.
        max_items: Maximum allowed selections (0 = unlimited).

    Returns:
        Error message if invalid, None if valid.
    """
    if min_items > 0 and len(selected) < min_items:
        return f"Select at least {min_items} item{'s' if min_items != 1 else ''}"
    if max_items > 0 and len(selected) > max_items:
        return f"Select at most {max_items} item{'s' if max_items != 1 else ''}"
    return None


def updateValidationError(
    errors: dict[str, str],
    field_name: str,
    error: Optional[str],
) -> dict[str, str]:
    """Update validation errors dict for a field.

    Args:
        errors: Current errors dict.
        field_name: Field being validated.
        error: Error message or None.

    Returns:
        Updated errors dict.
    """
    result = dict(errors)
    if error:
        result[field_name] = error
    elif field_name in result:
        del result[field_name]
    return result


def setField(values: dict[str, Any], field_name: str, value: Any) -> dict[str, Any]:
    """Set a field value in the form state.

    Args:
        values: Current form values dict.
        field_name: Field to set.
        value: New value.

    Returns:
        Updated values dict.
    """
    result = dict(values)
    result[field_name] = value
    return result


def unsetField(values: dict[str, Any], field_name: str) -> dict[str, Any]:
    """Remove a field value from the form state.

    Args:
        values: Current form values dict.
        field_name: Field to remove.

    Returns:
        Updated values dict.
    """
    result = dict(values)
    result.pop(field_name, None)
    return result


def commitTextField(
    values: dict[str, Any],
    field_name: str,
    text: str,
) -> dict[str, Any]:
    """Commit a text input value.

    Args:
        values: Current form values.
        field_name: Field name.
        text: Text to commit.

    Returns:
        Updated values dict.
    """
    return setField(values, field_name, text.strip())


def resolveFieldAsync(field_def: dict[str, Any], value: Any) -> str:
    """Resolve a field value for display (e.g. lookup labels).

    Args:
        field_def: Field definition dict.
        value: Current field value.

    Returns:
        Display string for the value.
    """
    if field_def.get("type") == "select":
        options = field_def.get("options", [])
        for opt in options:
            if isinstance(opt, dict) and opt.get("value") == value:
                return opt.get("label", str(value))
    return str(value) if value is not None else ""


def handleTextInputChange(current: str, new_char: str) -> str:
    """Handle a character input for text fields.

    Args:
        current: Current text value.
        new_char: New character to append.

    Returns:
        Updated text.
    """
    return current + new_char


def handleTextInputSubmit(text: str) -> str:
    """Handle text input submission.

    Args:
        text: Submitted text.

    Returns:
        Cleaned text value.
    """
    return text.strip()


def handleNavigation(
    current_index: int,
    total_fields: int,
    direction: str,
) -> int:
    """Handle field navigation in the dialog.

    Args:
        current_index: Current field index.
        total_fields: Total number of fields.
        direction: 'up' or 'down'.

    Returns:
        New field index.
    """
    if direction == "up":
        return max(0, current_index - 1)
    elif direction == "down":
        return min(total_fields - 1, current_index + 1)
    return current_index


def renderFormFields(fields: list[dict[str, Any]], values: dict[str, Any]) -> str:
    """Render all form fields for terminal display.

    Args:
        fields: List of field definitions.
        values: Current form values.

    Returns:
        Formatted form string.
    """
    if not fields:
        return f"{DIM}No input fields.{RESET}"

    lines = []
    for i, fdef in enumerate(fields):
        name = fdef.get("name", f"field_{i}")
        label = fdef.get("label", name)
        field_type = fdef.get("type", "text")
        required = fdef.get("required", False)
        value = values.get(name, "")

        req_mark = f"{RED}*{RESET}" if required else " "
        lines.append(f"  {req_mark} {BOLD}{label}{RESET} {DIM}({field_type}){RESET}")

        if field_type == "select":
            options = fdef.get("options", [])
            for opt in options:
                opt_label = opt.get("label", opt.get("value", "")) if isinstance(opt, dict) else str(opt)
                opt_value = opt.get("value", opt_label) if isinstance(opt, dict) else str(opt)
                marker = f"{GREEN}>{RESET}" if opt_value == value else " "
                lines.append(f"    {marker} {opt_label}")
        elif field_type == "multiselect":
            options = fdef.get("options", [])
            selected = value if isinstance(value, list) else []
            for opt in options:
                opt_label = opt.get("label", opt.get("value", "")) if isinstance(opt, dict) else str(opt)
                opt_value = opt.get("value", opt_label) if isinstance(opt, dict) else str(opt)
                marker = f"{GREEN}[x]{RESET}" if opt_value in selected else f"{DIM}[ ]{RESET}"
                lines.append(f"    {marker} {opt_label}")
        else:
            display_val = str(value) if value else f"{DIM}(empty){RESET}"
            lines.append(f"    > {display_val}")

        lines.append("")

    return "\n".join(lines)


def ElicitationDialog(
    title: str = "",
    fields: list[dict[str, Any]] | None = None,
    server_name: str = "",
    values: dict[str, Any] | None = None,
) -> str:
    """Format the full elicitation dialog for terminal display.

    Args:
        title: Dialog title.
        fields: List of field definitions.
        server_name: MCP server requesting input.
        values: Current form values.

    Returns:
        Formatted dialog string.
    """
    fields = fields or []
    values = values or {}

    lines = [
        "",
        f"{BOLD}{CYAN}--- Input Required ---{RESET}",
    ]

    if server_name:
        lines.append(f"  {DIM}Server:{RESET} {server_name}")
    if title:
        lines.append(f"  {BOLD}{title}{RESET}")

    lines.append("")
    lines.append(renderFormFields(fields, values))
    lines.append(
        f"  {GREEN}[enter]{RESET} submit  "
        f"{RED}[esc]{RESET} cancel  "
        f"{DIM}[tab]{RESET} next field"
    )
    lines.append(f"{BOLD}{CYAN}----------------------{RESET}")
    lines.append("")

    return "\n".join(lines)


def ElicitationFormDialog(
    fields: list[dict[str, Any]] | None = None,
    values: dict[str, Any] | None = None,
) -> str:
    """Format just the form portion of the elicitation dialog.

    Args:
        fields: Field definitions.
        values: Current values.

    Returns:
        Formatted form string.
    """
    return renderFormFields(fields or [], values or {})


def ElicitationURLDialog(
    url: str = "",
    label: str = "URL",
) -> str:
    """Format a URL input dialog.

    Args:
        url: Current URL value.
        label: Field label.

    Returns:
        Formatted URL input dialog.
    """
    display_url = url if url else f"{DIM}(enter URL){RESET}"
    return (
        f"  {BOLD}{label}:{RESET} {display_url}\n"
        f"  {DIM}Enter a valid URL and press Enter to continue{RESET}"
    )
