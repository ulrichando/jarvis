"""Elicitation input validation for MCP schema types."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal, Optional, Union


@dataclass
class ValidationResult:
    value: Optional[Union[str, int, float, bool]] = None
    is_valid: bool = False
    error: Optional[str] = None


# String format descriptions
STRING_FORMATS: dict[str, dict[str, str]] = {
    "email": {"description": "email address", "example": "user@example.com"},
    "uri": {"description": "URI", "example": "https://example.com"},
    "date": {"description": "date", "example": "2024-03-15"},
    "date-time": {"description": "date-time", "example": "2024-03-15T14:30:00Z"},
}


def is_enum_schema(schema: dict) -> bool:
    """Check if schema is a single-select enum."""
    return schema.get("type") == "string" and (
        "enum" in schema or "oneOf" in schema
    )


def is_multi_select_enum_schema(schema: dict) -> bool:
    """Check if schema is a multi-select enum."""
    if schema.get("type") != "array":
        return False
    items = schema.get("items")
    if not isinstance(items, dict):
        return False
    return "enum" in items or "anyOf" in items


def get_multi_select_values(schema: dict) -> list[str]:
    """Get values from a multi-select enum schema."""
    items = schema.get("items", {})
    if "anyOf" in items:
        return [item.get("const", "") for item in items["anyOf"]]
    if "enum" in items:
        return items["enum"]
    return []


def get_multi_select_labels(schema: dict) -> list[str]:
    """Get display labels from a multi-select enum schema."""
    items = schema.get("items", {})
    if "anyOf" in items:
        return [item.get("title", "") for item in items["anyOf"]]
    if "enum" in items:
        return items["enum"]
    return []


def get_enum_values(schema: dict) -> list[str]:
    """Get enum values from an EnumSchema."""
    if "oneOf" in schema:
        return [item.get("const", "") for item in schema["oneOf"]]
    if "enum" in schema:
        return schema["enum"]
    return []


def get_enum_labels(schema: dict) -> list[str]:
    """Get enum display labels."""
    if "oneOf" in schema:
        return [item.get("title", "") for item in schema["oneOf"]]
    if "enum" in schema:
        return schema.get("enumNames", schema["enum"])
    return []


def get_enum_label(schema: dict, value: str) -> str:
    """Get label for a specific enum value."""
    values = get_enum_values(schema)
    labels = get_enum_labels(schema)
    try:
        idx = values.index(value)
        return labels[idx] if idx < len(labels) else value
    except ValueError:
        return value


def validate_elicitation_input(
    string_value: str, schema: dict
) -> ValidationResult:
    """Validate an elicitation input against a schema."""
    schema_type = schema.get("type")

    if is_enum_schema(schema):
        values = get_enum_values(schema)
        if string_value in values:
            return ValidationResult(value=string_value, is_valid=True)
        return ValidationResult(
            is_valid=False,
            error=f"Must be one of: {', '.join(values)}",
        )

    if schema_type == "string":
        # Length validation
        min_len = schema.get("minLength")
        max_len = schema.get("maxLength")
        if min_len is not None and len(string_value) < min_len:
            return ValidationResult(
                is_valid=False,
                error=f"Must be at least {min_len} character(s)",
            )
        if max_len is not None and len(string_value) > max_len:
            return ValidationResult(
                is_valid=False,
                error=f"Must be at most {max_len} character(s)",
            )

        fmt = schema.get("format")
        if fmt == "email":
            if not re.match(r"^[^@]+@[^@]+\.[^@]+$", string_value):
                return ValidationResult(
                    is_valid=False,
                    error="Must be a valid email address, e.g. user@example.com",
                )
        elif fmt == "uri":
            if not re.match(r"^https?://", string_value):
                return ValidationResult(
                    is_valid=False,
                    error="Must be a valid URI, e.g. https://example.com",
                )
        elif fmt == "date":
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", string_value):
                return ValidationResult(
                    is_valid=False,
                    error="Must be a valid date, e.g. 2024-03-15",
                )
        elif fmt == "date-time":
            if not re.match(r"^\d{4}-\d{2}-\d{2}T", string_value):
                return ValidationResult(
                    is_valid=False,
                    error="Must be a valid date-time, e.g. 2024-03-15T14:30:00Z",
                )
        return ValidationResult(value=string_value, is_valid=True)

    if schema_type in ("number", "integer"):
        try:
            if schema_type == "integer":
                val = int(string_value)
            else:
                val = float(string_value)
        except ValueError:
            type_label = "an integer" if schema_type == "integer" else "a number"
            return ValidationResult(is_valid=False, error=f"Must be {type_label}")

        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if minimum is not None and val < minimum:
            return ValidationResult(
                is_valid=False, error=f"Must be >= {minimum}"
            )
        if maximum is not None and val > maximum:
            return ValidationResult(
                is_valid=False, error=f"Must be <= {maximum}"
            )
        return ValidationResult(value=val, is_valid=True)

    if schema_type == "boolean":
        lower = string_value.lower()
        if lower in ("true", "1", "yes"):
            return ValidationResult(value=True, is_valid=True)
        if lower in ("false", "0", "no"):
            return ValidationResult(value=False, is_valid=True)
        return ValidationResult(is_valid=False, error="Must be true or false")

    return ValidationResult(is_valid=False, error=f"Unsupported schema type: {schema_type}")


def get_format_hint(schema: dict) -> Optional[str]:
    """Returns a helpful placeholder/hint for a given format."""
    schema_type = schema.get("type")

    if schema_type == "string":
        fmt = schema.get("format")
        if fmt and fmt in STRING_FORMATS:
            info = STRING_FORMATS[fmt]
            return f"{info['description']}, e.g. {info['example']}"
        return None

    if schema_type in ("number", "integer"):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if minimum is not None and maximum is not None:
            return f"({schema_type} between {minimum} and {maximum})"
        elif minimum is not None:
            return f"({schema_type} >= {minimum})"
        elif maximum is not None:
            return f"({schema_type} <= {maximum})"
        else:
            example = "42" if schema_type == "integer" else "3.14"
            return f"({schema_type}, e.g. {example})"

    return None


def is_date_time_schema(schema: dict) -> bool:
    """Check if a schema is a date or date-time format."""
    return (
        schema.get("type") == "string"
        and schema.get("format") in ("date", "date-time")
    )
