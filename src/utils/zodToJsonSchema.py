"""
Converts Python dataclass/type definitions to JSON Schema.

This is a simplified equivalent of the TypeScript zodToJsonSchema utility.
Uses dataclasses-json or manual conversion for schema generation.
"""

from dataclasses import fields, is_dataclass
from typing import Any, Dict, Optional, Type, get_type_hints
import weakref


JsonSchema7Type = Dict[str, Any]

_cache: Dict[int, JsonSchema7Type] = {}


def python_type_to_json_schema(py_type: Any) -> JsonSchema7Type:
    """Convert a Python type annotation to JSON Schema."""
    if py_type is str:
        return {"type": "string"}
    elif py_type is int:
        return {"type": "integer"}
    elif py_type is float:
        return {"type": "number"}
    elif py_type is bool:
        return {"type": "boolean"}
    elif py_type is type(None):
        return {"type": "null"}
    elif hasattr(py_type, "__origin__"):
        origin = getattr(py_type, "__origin__", None)
        args = getattr(py_type, "__args__", ())
        if origin is list:
            item_schema = python_type_to_json_schema(args[0]) if args else {}
            return {"type": "array", "items": item_schema}
        elif origin is dict:
            value_schema = python_type_to_json_schema(args[1]) if len(args) > 1 else {}
            return {"type": "object", "additionalProperties": value_schema}
    elif is_dataclass(py_type):
        return dataclass_to_json_schema(py_type)
    return {}


def dataclass_to_json_schema(cls: Type) -> JsonSchema7Type:
    """Convert a dataclass to JSON Schema format."""
    cache_key = id(cls)
    if cache_key in _cache:
        return _cache[cache_key]

    hints = get_type_hints(cls)
    properties: Dict[str, Any] = {}
    required: list = []

    for f in fields(cls):
        prop_schema = python_type_to_json_schema(hints.get(f.name, str))
        properties[f.name] = prop_schema

        # Field is required if it has no default and no default_factory
        from dataclasses import MISSING
        if f.default is MISSING and f.default_factory is MISSING:
            required.append(f.name)

    result: JsonSchema7Type = {
        "type": "object",
        "properties": properties,
    }
    if required:
        result["required"] = required

    _cache[cache_key] = result
    return result


def zod_to_json_schema(schema: Any) -> JsonSchema7Type:
    """
    Converts a schema to JSON Schema format.

    Accepts dataclasses, type annotations, or pre-built JSON Schema dicts.
    """
    if isinstance(schema, dict):
        return schema

    if is_dataclass(schema):
        return dataclass_to_json_schema(schema)

    return python_type_to_json_schema(schema)
