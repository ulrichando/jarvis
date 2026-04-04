"""CA certificate loading utilities."""

from __future__ import annotations

import logging
import os
import ssl
from functools import lru_cache
from typing import Optional

logger = logging.getLogger(__name__)

_cache_cleared = False


@lru_cache(maxsize=1)
def get_ca_certificates() -> Optional[list[str]]:
    """Load CA certificates for TLS connections.

    Returns None when no custom CA configuration is needed.
    """
    extra_certs_path = os.environ.get("NODE_EXTRA_CA_CERTS") or os.environ.get(
        "SSL_CERT_FILE"
    )
    use_system_ca = os.environ.get("SSL_CERT_DIR") is not None

    logger.debug(
        f"CA certs: use_system_ca={use_system_ca}, extra_certs_path={extra_certs_path}"
    )

    if not use_system_ca and not extra_certs_path:
        return None

    certs: list[str] = []

    # Load default CA bundle
    default_context = ssl.create_default_context()
    ca_certs_file = default_context.get_ca_certs()
    if ca_certs_file:
        logger.debug(f"CA certs: loaded {len(ca_certs_file)} default CA certificates")

    if extra_certs_path:
        try:
            with open(extra_certs_path, "r") as f:
                extra_cert = f.read()
            certs.append(extra_cert)
            logger.debug(
                f"CA certs: Appended extra certificates from {extra_certs_path}"
            )
        except Exception as e:
            logger.error(
                f"CA certs: Failed to read extra certs file ({extra_certs_path}): {e}"
            )

    return certs if certs else None


def clear_ca_certs_cache() -> None:
    """Clear the CA certificates cache."""
    get_ca_certificates.cache_clear()
    logger.debug("Cleared CA certificates cache")
