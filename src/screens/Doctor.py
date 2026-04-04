"""Doctor screen -- diagnostic checks for system health."""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from typing import Optional


@dataclass
class DiagnosticResult:
    name: str
    status: str  # 'pass' | 'warn' | 'fail'
    message: str
    detail: str = ""


async def run_doctor() -> list[DiagnosticResult]:
    """Run diagnostic checks and return results."""
    results: list[DiagnosticResult] = []

    # Check Python version
    version = sys.version_info
    if version >= (3, 10):
        results.append(DiagnosticResult("Python Version", "pass", f"{version.major}.{version.minor}.{version.micro}"))
    else:
        results.append(DiagnosticResult("Python Version", "fail", f"{version.major}.{version.minor} (3.10+ required)"))

    # Check git
    if shutil.which("git"):
        results.append(DiagnosticResult("Git", "pass", "Found"))
    else:
        results.append(DiagnosticResult("Git", "warn", "Not found"))

    # Check config directory
    config_dir = os.path.expanduser("~/.jarvis")
    if os.path.isdir(config_dir):
        results.append(DiagnosticResult("Config Directory", "pass", config_dir))
    else:
        results.append(DiagnosticResult("Config Directory", "warn", "Not found"))

    return results
