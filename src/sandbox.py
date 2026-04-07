"""JARVIS sandbox — JARVIS Sandbox — Linux namespace isolation for bash commands.
# JARVIS sandbox

# JARVIS sandbox - Core sandboxing for secure command execution

Ported from claw-code's sandbox.rs. Uses Linux `unshare` to create
isolated environments for command execution:
- User namespace isolation (--user --map-root-user)
- Mount namespace (--mount)
- IPC namespace (--ipc)
- PID namespace (--pid --fork)
- UTS namespace (--uts)
- Optional network isolation (--net)
- Filesystem allow-list mode
"""

import os
import shlex
import shutil
import subprocess
import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

log = logging.getLogger("jarvis.sandbox")


class FilesystemMode(Enum):
    OFF = "off"                    # No restrictions
    WORKSPACE_ONLY = "workspace"   # Only workspace access
    ALLOW_LIST = "allowlist"       # Whitelist specific paths


@dataclass
class SandboxConfig:
    """Configuration for sandbox execution."""
    enabled: bool = True
    namespace_isolation: bool = True
    network_isolation: bool = False
    filesystem_mode: FilesystemMode = FilesystemMode.OFF
    allowed_mounts: list[str] = field(default_factory=list)
    timeout: int = 60


@dataclass
class SandboxStatus:
    """Status of sandbox capabilities on this system."""
    available: bool = False
    unshare_path: str = ""
    in_container: bool = False
    namespace_support: bool = False
    network_support: bool = False
    fallback_reasons: list[str] = field(default_factory=list)


def detect_sandbox_capabilities() -> SandboxStatus:
    """Detect what sandbox features are available on this system."""
    status = SandboxStatus()

    # Check if we're already in a container
    status.in_container = _detect_container()
    if status.in_container:
        status.fallback_reasons.append("Running inside container")

    # Check for unshare binary
    unshare = shutil.which("unshare")
    if unshare:
        status.unshare_path = unshare
        status.available = True
        status.namespace_support = True
        status.network_support = True
    else:
        status.fallback_reasons.append("unshare not found")

    # Check if on Linux
    import platform
    if platform.system() != "Linux":
        status.available = False
        status.namespace_support = False
        status.network_support = False
        status.fallback_reasons.append(f"Not Linux ({platform.system()})")

    return status


def _detect_container() -> bool:
    """Detect if running inside a container (Docker, Podman, K8s)."""
    # Check for container markers
    if os.path.exists("/.dockerenv"):
        return True
    if os.path.exists("/run/.containerenv"):
        return True

    # Check environment variables
    for var in ("CONTAINER", "DOCKER", "PODMAN", "KUBERNETES_SERVICE_HOST"):
        if os.environ.get(var):
            return True

    # Check cgroup
    try:
        cgroup = Path("/proc/1/cgroup").read_text()
        for marker in ("docker", "containerd", "kubepods", "podman", "libpod"):
            if marker in cgroup:
                return True
    except (OSError, PermissionError):
        pass

    return False


def build_sandbox_command(
    command: str,
    config: SandboxConfig,
    cwd: str = None,
) -> tuple[str, dict]:
    """Wrap a command with sandbox isolation.

    Returns (wrapped_command, extra_env).
    Falls back to raw command if sandbox unavailable.
    """
    status = detect_sandbox_capabilities()

    if not config.enabled or not status.available:
        return command, {}

    parts = [status.unshare_path]

    # Namespace flags
    if config.namespace_isolation and status.namespace_support:
        parts.extend(["--user", "--map-root-user", "--mount", "--ipc", "--pid", "--uts", "--fork"])

    # Network isolation
    if config.network_isolation and status.network_support:
        parts.append("--net")

    # Build the inner command with environment
    sandbox_home = os.path.join("/tmp", "jarvis-sandbox-home")
    sandbox_tmp = os.path.join("/tmp", "jarvis-sandbox-tmp")

    extra_env = {
        "HOME": sandbox_home,
        "TMPDIR": sandbox_tmp,
        "JARVIS_SANDBOX": "1",
        "JARVIS_SANDBOX_FILESYSTEM_MODE": config.filesystem_mode.value,
    }

    if config.allowed_mounts:
        extra_env["JARVIS_SANDBOX_ALLOWED_MOUNTS"] = ":".join(config.allowed_mounts)

    # Wrap: unshare [flags] bash -lc 'command' — shlex.quote ensures the
    # inner script is passed as a single argument through the outer sh -c shell.
    inner_cmd = f"mkdir -p {sandbox_home} {sandbox_tmp} 2>/dev/null; {command}"
    parts.extend(["bash", "-lc", shlex.quote(inner_cmd)])

    return " ".join(parts), extra_env


def execute_sandboxed(
    command: str,
    config: SandboxConfig = None,
    cwd: str = None,
    timeout: int = None,
) -> dict:
    """Execute a command in a sandbox. Returns dict with stdout, stderr, returncode."""
    if config is None:
        config = SandboxConfig()

    timeout = timeout or config.timeout
    wrapped_cmd, extra_env = build_sandbox_command(command, config, cwd)

    env = {**os.environ, **extra_env}

    try:
        result = subprocess.run(
            wrapped_cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd or os.getcwd(),
            env=env,
        )
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
            "sandboxed": config.enabled and detect_sandbox_capabilities().available,
        }
    except subprocess.TimeoutExpired:
        return {
            "stdout": "",
            "stderr": f"Sandbox command timed out after {timeout}s",
            "returncode": -1,
            "sandboxed": True,
        }
    except Exception as e:
        return {
            "stdout": "",
            "stderr": str(e),
            "returncode": -1,
            "sandboxed": False,
        }
