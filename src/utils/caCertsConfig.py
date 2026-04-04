"""Config/settings-backed CA certs population.

Split from caCerts.py to avoid circular dependencies.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def apply_extra_ca_certs_from_config() -> None:
    """Apply NODE_EXTRA_CA_CERTS / SSL_CERT_FILE from config to process env.

    Should be called early in init, before any TLS connections.
    """
    if os.environ.get("NODE_EXTRA_CA_CERTS") or os.environ.get("SSL_CERT_FILE"):
        return

    config_path = _get_extra_certs_path_from_config()
    if config_path:
        os.environ["SSL_CERT_FILE"] = config_path
        logger.debug(
            f"CA certs: Applied SSL_CERT_FILE from config to process env: {config_path}"
        )


def _get_extra_certs_path_from_config() -> Optional[str]:
    """Read extra CA certs path from config as a fallback."""
    try:
        home = os.path.expanduser("~")
        config_dir = os.path.join(home, ".jarvis")

        # Try reading from global config
        import json

        config_path = os.path.join(config_dir, "config.json")
        if os.path.exists(config_path):
            with open(config_path) as f:
                config = json.load(f)
            env_config = config.get("env", {})
            path = env_config.get("NODE_EXTRA_CA_CERTS") or env_config.get(
                "SSL_CERT_FILE"
            )
            if path:
                logger.debug(f"CA certs: Found extra certs path in config: {path}")
                return path

        return None
    except Exception as e:
        logger.debug(f"CA certs: Config fallback failed: {e}")
        return None
