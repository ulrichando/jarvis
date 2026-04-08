"""SecretRef — 3-source secret resolution (env / file / exec).

Mirrors OpenClaw's SecretRef pattern.  Providers and hooks can store API
keys as literals or as references that are resolved lazily at runtime.

Usage:
    ref = SecretRef(source="env",  id="OPENAI_API_KEY")
    ref = SecretRef(source="file", id="~/.jarvis/openai_key.txt")
    ref = SecretRef(source="exec", id="op read op://Jarvis/OpenAI/key")

    key = resolve_secret(ref)           # resolves
    key = resolve_secret("sk-literal")  # passes through unchanged
"""

from __future__ import annotations

import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Union


@dataclass
class SecretRef:
    source: str  # "env" | "file" | "exec"
    id: str
    provider: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "SecretRef":
        return cls(
            source=data["source"],
            id=data["id"],
            provider=data.get("provider", ""),
        )

    def to_dict(self) -> dict:
        d: dict = {"source": self.source, "id": self.id}
        if self.provider:
            d["provider"] = self.provider
        return d

    def __repr__(self) -> str:
        return f"SecretRef(source={self.source!r}, id={self.id!r})"


# Accepts either a literal string or a SecretRef
SecretValue = Union[str, "SecretRef"]


def resolve_secret(value: SecretValue, default: str = "") -> str:
    """Resolve a secret from any of the three sources.

    Args:
        value:   A plain string (returned as-is) or a SecretRef.
        default: Returned when resolution fails.

    Returns:
        The resolved secret string.
    """
    if isinstance(value, str):
        return value  # literal — use as-is

    if not isinstance(value, SecretRef):
        return default

    try:
        if value.source == "env":
            return os.environ.get(value.id, default)

        elif value.source == "file":
            path = Path(value.id).expanduser()
            return path.read_text(encoding="utf-8").strip()

        elif value.source == "exec":
            cmd = shlex.split(value.id)
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=10, check=False
            )
            if result.returncode != 0:
                return default
            return result.stdout.strip()

        else:
            return default

    except Exception:
        return default


def secret_ref_from_config(value: str | dict) -> SecretValue:
    """Parse a config value that may be a literal string or a SecretRef dict.

    Accepts::

        "sk-literal"                          → literal string
        {"source": "env", "id": "KEY_NAME"}  → SecretRef
    """
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and "source" in value and "id" in value:
        return SecretRef.from_dict(value)
    return str(value)
