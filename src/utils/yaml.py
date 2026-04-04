"""
YAML parsing wrapper.
"""

from __future__ import annotations

from typing import Any

import yaml as _yaml


def parse_yaml(input_str: str) -> Any:
    """Parse a YAML string and return the result."""
    return _yaml.safe_load(input_str)
