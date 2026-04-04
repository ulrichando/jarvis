"""Product URLs and remote session helpers."""

from typing import Optional

PRODUCT_URL: str = "https://claude.com/claude-code"

# Remote session URLs
CLAUDE_AI_BASE_URL: str = "https://claude.ai"
CLAUDE_AI_STAGING_BASE_URL: str = "https://claude-ai.staging.ant.dev"
CLAUDE_AI_LOCAL_BASE_URL: str = "http://localhost:4000"


def is_remote_session_staging(
    session_id: Optional[str] = None,
    ingress_url: Optional[str] = None,
) -> bool:
    """Determine if we're in a staging environment for remote sessions.
    Checks session ID format and ingress URL.
    """
    return (
        (session_id is not None and "_staging_" in session_id)
        or (ingress_url is not None and "staging" in ingress_url)
    )


def is_remote_session_local(
    session_id: Optional[str] = None,
    ingress_url: Optional[str] = None,
) -> bool:
    """Determine if we're in a local-dev environment for remote sessions.
    Checks session ID format (e.g. `session_local_...`) and ingress URL.
    """
    return (
        (session_id is not None and "_local_" in session_id)
        or (ingress_url is not None and "localhost" in ingress_url)
    )


def get_claude_ai_base_url(
    session_id: Optional[str] = None,
    ingress_url: Optional[str] = None,
) -> str:
    """Get the base URL for Claude AI based on environment."""
    if is_remote_session_local(session_id, ingress_url):
        return CLAUDE_AI_LOCAL_BASE_URL
    if is_remote_session_staging(session_id, ingress_url):
        return CLAUDE_AI_STAGING_BASE_URL
    return CLAUDE_AI_BASE_URL


def get_remote_session_url(
    session_id: str,
    ingress_url: Optional[str] = None,
) -> str:
    """Get the full session URL for a remote session.

    The cse_ -> session_ translation is a temporary shim. Worker endpoints
    (/v1/code/sessions/{id}/worker/*) want `cse_*` but the claude.ai frontend
    currently routes on `session_*`. Same UUID body, different tag prefix.
    """
    # In the TS version this lazy-imports toCompatSessionId from bridge.
    # For the Python port we do a simple prefix replacement inline.
    compat_id = session_id.replace("cse_", "session_", 1) if session_id.startswith("cse_") else session_id
    base_url = get_claude_ai_base_url(compat_id, ingress_url)
    return f"{base_url}/code/{compat_id}"
