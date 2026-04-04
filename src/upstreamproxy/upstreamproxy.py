"""CCR upstreamproxy -- container-side wiring.

Sets up the upstream proxy for CCR session containers, including:
- Reading session tokens
- Setting PR_SET_DUMPABLE to block ptrace
- Downloading and concatenating CA certificates
- Starting a local CONNECT->WebSocket relay
- Exposing HTTPS_PROXY / SSL_CERT_FILE env vars
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

SESSION_TOKEN_PATH = "/run/ccr/session_token"
SYSTEM_CA_BUNDLE = "/etc/ssl/certs/ca-certificates.crt"

NO_PROXY_LIST = ",".join([
    "localhost", "127.0.0.1", "::1",
    "169.254.0.0/16", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
    "anthropic.com", ".anthropic.com", "*.anthropic.com",
    "github.com", "api.github.com", "*.github.com", "*.githubusercontent.com",
    "registry.npmjs.org", "pypi.org", "files.pythonhosted.org",
    "index.crates.io", "proxy.golang.org",
])


@dataclass
class UpstreamProxyState:
    enabled: bool = False
    port: Optional[int] = None
    ca_bundle_path: Optional[str] = None


_state = UpstreamProxyState()


def _is_env_truthy(value: Optional[str]) -> bool:
    return (value or "").lower() in ("1", "true", "yes")


async def init_upstream_proxy(
    token_path: Optional[str] = None,
    system_ca_path: Optional[str] = None,
    ca_bundle_path: Optional[str] = None,
    ccr_base_url: Optional[str] = None,
) -> UpstreamProxyState:
    """Initialize upstreamproxy. Safe to call when feature is off."""
    global _state

    if not _is_env_truthy(os.environ.get("CLAUDE_CODE_REMOTE")):
        return _state
    if not _is_env_truthy(os.environ.get("CCR_UPSTREAM_PROXY_ENABLED")):
        return _state

    session_id = os.environ.get("CLAUDE_CODE_REMOTE_SESSION_ID")
    if not session_id:
        logger.warning("[upstreamproxy] CLAUDE_CODE_REMOTE_SESSION_ID unset; proxy disabled")
        return _state

    actual_token_path = token_path or SESSION_TOKEN_PATH
    token = _read_token(actual_token_path)
    if not token:
        logger.debug("[upstreamproxy] no session token file; proxy disabled")
        return _state

    _set_non_dumpable()

    base_url = ccr_base_url or os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
    actual_ca_path = ca_bundle_path or os.path.join(Path.home(), ".ccr", "ca-bundle.crt")
    actual_system_ca = system_ca_path or SYSTEM_CA_BUNDLE

    ca_ok = await _download_ca_bundle(base_url, actual_system_ca, actual_ca_path)
    if not ca_ok:
        return _state

    try:
        from .relay import start_upstream_proxy_relay
        ws_url = base_url.replace("http", "ws", 1) + "/v1/code/upstreamproxy/ws"
        relay = await start_upstream_proxy_relay(ws_url=ws_url, session_id=session_id, token=token)
        _state = UpstreamProxyState(enabled=True, port=relay.port, ca_bundle_path=actual_ca_path)
        logger.debug("[upstreamproxy] enabled on 127.0.0.1:%d", relay.port)

        try:
            os.unlink(actual_token_path)
        except Exception:
            logger.warning("[upstreamproxy] token file unlink failed")
    except Exception as err:
        logger.warning("[upstreamproxy] relay start failed: %s; proxy disabled", err)

    return _state


def get_upstream_proxy_env() -> dict[str, str]:
    """Env vars to merge into every agent subprocess."""
    if not _state.enabled or not _state.port or not _state.ca_bundle_path:
        # Pass through inherited proxy vars if present
        if os.environ.get("HTTPS_PROXY") and os.environ.get("SSL_CERT_FILE"):
            inherited = {}
            for key in ["HTTPS_PROXY", "https_proxy", "NO_PROXY", "no_proxy",
                        "SSL_CERT_FILE", "NODE_EXTRA_CA_CERTS", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"]:
                val = os.environ.get(key)
                if val:
                    inherited[key] = val
            return inherited
        return {}

    proxy_url = f"http://127.0.0.1:{_state.port}"
    return {
        "HTTPS_PROXY": proxy_url,
        "https_proxy": proxy_url,
        "NO_PROXY": NO_PROXY_LIST,
        "no_proxy": NO_PROXY_LIST,
        "SSL_CERT_FILE": _state.ca_bundle_path,
        "NODE_EXTRA_CA_CERTS": _state.ca_bundle_path,
        "REQUESTS_CA_BUNDLE": _state.ca_bundle_path,
        "CURL_CA_BUNDLE": _state.ca_bundle_path,
    }


def reset_upstream_proxy_for_tests() -> None:
    """Test-only: reset module state."""
    global _state
    _state = UpstreamProxyState()


def _read_token(path: str) -> Optional[str]:
    try:
        with open(path) as f:
            raw = f.read().strip()
            return raw or None
    except FileNotFoundError:
        return None
    except Exception as err:
        logger.warning("[upstreamproxy] token read failed: %s", err)
        return None


def _set_non_dumpable() -> None:
    """Set PR_SET_DUMPABLE to 0 to block same-UID ptrace."""
    if sys.platform != "linux":
        return
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        PR_SET_DUMPABLE = 4
        rc = libc.prctl(PR_SET_DUMPABLE, 0, 0, 0, 0)
        if rc != 0:
            logger.warning("[upstreamproxy] prctl(PR_SET_DUMPABLE,0) returned nonzero")
    except Exception as err:
        logger.warning("[upstreamproxy] prctl unavailable: %s", err)


async def _download_ca_bundle(base_url: str, system_ca_path: str, out_path: str) -> bool:
    """Download the upstream proxy CA cert and concatenate with system bundle."""
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{base_url}/v1/code/upstreamproxy/ca-cert",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status != 200:
                    logger.warning("[upstreamproxy] ca-cert fetch %d; proxy disabled", resp.status)
                    return False
                ccr_ca = await resp.text()

        system_ca = ""
        try:
            with open(system_ca_path) as f:
                system_ca = f.read()
        except Exception:
            pass

        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w") as f:
            f.write(system_ca + "\n" + ccr_ca)
        return True
    except Exception as err:
        logger.warning("[upstreamproxy] ca-cert download failed: %s; proxy disabled", err)
        return False
