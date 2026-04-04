"""
Internal logging utilities.

Functions for Kubernetes namespace detection, container ID retrieval,
and permission context logging (primarily for internal/ant users).
"""

from __future__ import annotations

import logging
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
async def get_kubernetes_namespace() -> Optional[str]:
    """Get the current Kubernetes namespace.

    Returns None on local development, 'default' for default namespace, etc.
    """
    if os.environ.get("USER_TYPE") != "ant":
        return None

    namespace_path = Path("/var/run/secrets/kubernetes.io/serviceaccount/namespace")
    try:
        return namespace_path.read_text().strip()
    except Exception:
        return "namespace not found"


@lru_cache(maxsize=1)
async def get_container_id() -> Optional[str]:
    """Get the OCI container ID from within a running container."""
    if os.environ.get("USER_TYPE") != "ant":
        return None

    container_id_path = Path("/proc/self/mountinfo")
    pattern = re.compile(r"(?:/docker/containers/|/sandboxes/)([0-9a-f]{64})")

    try:
        mountinfo = container_id_path.read_text().strip()
        for line in mountinfo.split("\n"):
            match = pattern.search(line)
            if match:
                return match.group(1)
        return "container ID not found in mountinfo"
    except Exception:
        return "container ID not found"


async def log_permission_context_for_ants(
    tool_permission_context: Any,
    moment: str,  # 'summary' | 'initialization'
) -> None:
    """Log an event with the current namespace and tool permission context."""
    if os.environ.get("USER_TYPE") != "ant":
        return

    # In a full implementation, this would call logEvent
    namespace = await get_kubernetes_namespace()
    container_id = await get_container_id()
    logger.debug(
        f"Permission context [{moment}]: namespace={namespace}, "
        f"container={container_id}"
    )
