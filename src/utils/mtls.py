"""mTLS (mutual TLS) configuration from environment variables."""

from __future__ import annotations

import os
import ssl
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional


@dataclass
class MTLSConfig:
    """mTLS certificate configuration."""
    cert: Optional[str] = None
    key: Optional[str] = None
    passphrase: Optional[str] = None


@dataclass
class TLSConfig(MTLSConfig):
    """TLS configuration including CA certificates."""
    ca: Optional[str] = None


@lru_cache(maxsize=1)
def get_mtls_config() -> Optional[MTLSConfig]:
    """Get mTLS configuration from environment variables."""
    config = MTLSConfig()

    cert_path = os.environ.get("CLAUDE_CODE_CLIENT_CERT")
    if cert_path:
        try:
            with open(cert_path, "r") as f:
                config.cert = f.read()
        except OSError:
            pass

    key_path = os.environ.get("CLAUDE_CODE_CLIENT_KEY")
    if key_path:
        try:
            with open(key_path, "r") as f:
                config.key = f.read()
        except OSError:
            pass

    passphrase = os.environ.get("CLAUDE_CODE_CLIENT_KEY_PASSPHRASE")
    if passphrase:
        config.passphrase = passphrase

    # Only return if at least one option is set
    if not config.cert and not config.key and not config.passphrase:
        return None
    return config


def create_ssl_context(
    mtls_config: Optional[MTLSConfig] = None,
    ca_file: Optional[str] = None,
) -> ssl.SSLContext:
    """Create an SSL context with optional mTLS and CA configuration."""
    ctx = ssl.create_default_context()

    if ca_file:
        ctx.load_verify_locations(ca_file)

    if mtls_config:
        if mtls_config.cert and mtls_config.key:
            # Write cert/key to temp files if they're PEM strings
            # For real use, these would typically be file paths
            ctx.load_cert_chain(
                certfile=mtls_config.cert,
                keyfile=mtls_config.key,
                password=mtls_config.passphrase,
            )

    return ctx


def clear_mtls_cache() -> None:
    """Clear the mTLS configuration cache."""
    get_mtls_config.cache_clear()


def configure_global_mtls() -> None:
    """Configure global TLS settings from environment."""
    extra_ca = os.environ.get("NODE_EXTRA_CA_CERTS")
    if extra_ca:
        # Python's ssl module handles CA certs differently
        # This is a placeholder for environment-specific configuration
        pass
