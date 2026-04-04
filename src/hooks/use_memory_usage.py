"""Monitor process memory usage."""

from __future__ import annotations

import os
import resource
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class MemoryUsageStatus(Enum):
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


# 1.5GB and 2.5GB thresholds in bytes
HIGH_MEMORY_THRESHOLD = 1.5 * 1024 * 1024 * 1024
CRITICAL_MEMORY_THRESHOLD = 2.5 * 1024 * 1024 * 1024


@dataclass
class MemoryUsageInfo:
    heap_used: int
    status: MemoryUsageStatus


def get_memory_usage() -> Optional[MemoryUsageInfo]:
    """Get current process memory usage.

    Returns None if memory usage is normal.
    Equivalent to useMemoryUsage React hook (polls every 10s).
    """
    try:
        # resource.getrusage returns maxrss in KB on Linux, bytes on macOS
        usage = resource.getrusage(resource.RUSAGE_SELF)
        # Linux reports in KB, macOS in bytes
        import sys

        if sys.platform == "linux":
            heap_used = usage.ru_maxrss * 1024
        else:
            heap_used = usage.ru_maxrss
    except Exception:
        try:
            # Fallback: read from /proc/self/status
            with open("/proc/self/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        heap_used = int(line.split()[1]) * 1024  # KB to bytes
                        break
                else:
                    return None
        except (OSError, ValueError):
            return None

    if heap_used >= CRITICAL_MEMORY_THRESHOLD:
        status = MemoryUsageStatus.CRITICAL
    elif heap_used >= HIGH_MEMORY_THRESHOLD:
        status = MemoryUsageStatus.HIGH
    else:
        return None  # Normal - don't report

    return MemoryUsageInfo(heap_used=heap_used, status=status)
