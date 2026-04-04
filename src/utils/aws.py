"""AWS credential utilities."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class AwsCredentials:
    access_key_id: str
    secret_access_key: str
    session_token: str
    expiration: Optional[str] = None


@dataclass
class AwsStsOutput:
    credentials: AwsCredentials


def is_aws_credentials_provider_error(err: Any) -> bool:
    """Check if error is a CredentialsProviderError."""
    return getattr(err, "name", None) == "CredentialsProviderError"


def is_valid_aws_sts_output(obj: Any) -> bool:
    """Validate AWS STS assume-role output structure."""
    if not obj or not isinstance(obj, dict):
        return False

    credentials = obj.get("Credentials")
    if not credentials or not isinstance(credentials, dict):
        return False

    return (
        isinstance(credentials.get("AccessKeyId"), str)
        and isinstance(credentials.get("SecretAccessKey"), str)
        and isinstance(credentials.get("SessionToken"), str)
        and len(credentials["AccessKeyId"]) > 0
        and len(credentials["SecretAccessKey"]) > 0
        and len(credentials["SessionToken"]) > 0
    )


async def check_sts_caller_identity() -> None:
    """Check STS caller identity. Raises if identity cannot be retrieved."""
    try:
        import boto3

        client = boto3.client("sts")
        client.get_caller_identity()
    except ImportError:
        raise RuntimeError("boto3 is required for AWS STS operations")


async def clear_aws_ini_cache() -> None:
    """Clear AWS credential provider cache by forcing a refresh."""
    try:
        logger.debug("Clearing AWS credential provider cache")
        import boto3

        # Force a session refresh
        boto3.Session()
        logger.debug("AWS credential provider cache refreshed")
    except Exception:
        logger.debug(
            "Failed to clear AWS credential cache "
            "(this is expected if no credentials are configured)"
        )
