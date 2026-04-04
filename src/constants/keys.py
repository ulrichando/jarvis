"""GrowthBook client key configuration."""

import os


def get_growthbook_client_key() -> str:
    """Get the GrowthBook client key based on user type and dev mode."""
    user_type = os.environ.get("USER_TYPE")
    enable_dev = os.environ.get("ENABLE_GROWTHBOOK_DEV", "").lower() in (
        "1", "true", "yes",
    )

    if user_type == "ant":
        if enable_dev:
            return "sdk-yZQvlplybuXjYh6L"
        return "sdk-xRVcrliHIlrg4og4"
    return "sdk-zAZezfDKGoZuXXKe"
