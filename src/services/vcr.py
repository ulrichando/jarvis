"""
VCR (Video Cassette Recorder) - fixture-based test recording/replay.

Records API responses to fixture files during tests, and replays
them in subsequent runs for deterministic testing without network calls.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional, TypeVar

T = TypeVar("T")


def should_use_vcr() -> bool:
    """Check if VCR recording/replay should be used."""
    if os.environ.get("NODE_ENV") == "test":
        return True
    if os.environ.get("USER_TYPE") == "ant" and os.environ.get("FORCE_VCR"):
        return True
    return False


async def with_fixture(
    input_data: Any,
    fixture_name: str,
    f: Callable[[], Any],
) -> Any:
    """Generic fixture management helper.

    Handles caching, reading, writing fixtures for any data type.
    """
    if not should_use_vcr():
        return await f()

    # Create hash of input for fixture filename
    hash_hex = hashlib.sha1(
        json.dumps(input_data, default=str).encode()
    ).hexdigest()[:12]

    cwd = os.environ.get("CLAUDE_CODE_TEST_FIXTURES_ROOT", os.getcwd())
    filename = Path(cwd) / f"fixtures/{fixture_name}-{hash_hex}.json"

    # Try to read cached fixture
    try:
        if filename.exists():
            return json.loads(filename.read_text())
    except Exception as e:
        if not isinstance(e, FileNotFoundError):
            raise

    is_ci = os.environ.get("CI") and not os.environ.get("VCR_RECORD")
    if is_ci:
        raise FileNotFoundError(
            f"Fixture missing: {filename}. Re-run tests with VCR_RECORD=1."
        )

    # Create & write new fixture
    result = await f()
    filename.parent.mkdir(parents=True, exist_ok=True)
    filename.write_text(json.dumps(result, indent=2, default=str))
    return result


async def with_vcr(
    messages: List[Any],
    f: Callable[[], Any],
) -> List[Any]:
    """Record/replay API responses for testing."""
    if not should_use_vcr():
        return await f()

    # Simplified fixture key generation
    key_data = json.dumps(
        [_dehydrate_value(m) for m in messages], default=str
    )
    hash_parts = [
        hashlib.sha1(json.dumps(item, default=str).encode()).hexdigest()[:6]
        for item in messages
    ]

    cwd = os.environ.get("CLAUDE_CODE_TEST_FIXTURES_ROOT", os.getcwd())
    filename = Path(cwd) / f"fixtures/{'-'.join(hash_parts)}.json"

    # Try cached fixture
    try:
        if filename.exists():
            cached = json.loads(filename.read_text())
            return cached.get("output", [])
    except Exception:
        pass

    is_ci = os.environ.get("CI") and not os.environ.get("VCR_RECORD")
    if is_ci:
        raise FileNotFoundError(f"API fixture missing: {filename}")

    # Create new fixture
    results = await f()
    filename.parent.mkdir(parents=True, exist_ok=True)
    filename.write_text(json.dumps({"output": results}, indent=2, default=str))
    return results


async def with_token_count_vcr(
    messages: List[Any],
    tools: List[Any],
    f: Callable[[], Any],
) -> Optional[int]:
    """Record/replay token count responses."""
    dehydrated = _dehydrate_value(json.dumps({"messages": messages, "tools": tools}, default=str))
    result = await with_fixture(dehydrated, "token-count", f)
    if isinstance(result, dict):
        return result.get("tokenCount")
    return result


def _dehydrate_value(s: Any) -> Any:
    """Replace environment-specific values with placeholders."""
    if not isinstance(s, str):
        return s
    cwd = os.getcwd()
    config_home = os.environ.get("JARVIS_HOME", os.path.expanduser("~/.jarvis"))
    return s.replace(config_home, "[CONFIG_HOME]").replace(cwd, "[CWD]")


def _hydrate_value(s: Any) -> Any:
    """Replace placeholders with environment-specific values."""
    if not isinstance(s, str):
        return s
    cwd = os.getcwd()
    config_home = os.environ.get("JARVIS_HOME", os.path.expanduser("~/.jarvis"))
    return s.replace("[CONFIG_HOME]", config_home).replace("[CWD]", cwd)
