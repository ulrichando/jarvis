"""
Temporary file path generation.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import uuid
from typing import Optional


def generate_temp_file_path(
    prefix: str = "jarvis-prompt",
    extension: str = ".md",
    content_hash: Optional[str] = None,
) -> str:
    """
    Generate a temporary file path.

    Args:
        prefix: Prefix for the temp file name.
        extension: File extension (defaults to '.md').
        content_hash: When provided, the identifier is derived from a
            SHA-256 hash. This produces a stable path across processes.

    Returns:
        Temp file path.
    """
    if content_hash:
        file_id = hashlib.sha256(content_hash.encode()).hexdigest()[:16]
    else:
        file_id = str(uuid.uuid4())

    return os.path.join(tempfile.gettempdir(), f"{prefix}-{file_id}{extension}")
