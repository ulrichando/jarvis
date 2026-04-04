"""
Utility functions for the WebFetchTool.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

from src.tools.WebFetchTool.preapproved import is_preapproved_host

MAX_URL_LENGTH = 2000
MAX_HTTP_CONTENT_LENGTH = 10 * 1024 * 1024  # 10 MB
FETCH_TIMEOUT_S = 60
DOMAIN_CHECK_TIMEOUT_S = 10
MAX_REDIRECTS = 10
MAX_MARKDOWN_LENGTH = 100_000


def is_preapproved_url(url: str) -> bool:
    """Check if a URL is preapproved."""
    try:
        parsed = urlparse(url)
        return is_preapproved_host(parsed.hostname or "", parsed.path)
    except Exception:
        return False


def validate_url(url: str) -> bool:
    """Validate a URL for safety and correctness."""
    if len(url) > MAX_URL_LENGTH:
        return False

    try:
        parsed = urlparse(url)
    except Exception:
        return False

    if parsed.username or parsed.password:
        return False

    hostname = parsed.hostname or ""
    parts = hostname.split(".")
    if len(parts) < 2:
        return False

    return True


def is_permitted_redirect(original_url: str, redirect_url: str) -> bool:
    """Check if a redirect is safe to follow.

    Allows redirects that:
    - Add or remove "www." in the hostname
    - Keep the origin the same but change path/query params
    """
    try:
        parsed_original = urlparse(original_url)
        parsed_redirect = urlparse(redirect_url)

        if parsed_redirect.scheme != parsed_original.scheme:
            return False

        if (parsed_redirect.port or 443) != (parsed_original.port or 443):
            return False

        if parsed_redirect.username or parsed_redirect.password:
            return False

        def strip_www(hostname: str) -> str:
            return hostname.removeprefix("www.")

        original_host = strip_www(parsed_original.hostname or "")
        redirect_host = strip_www(parsed_redirect.hostname or "")
        return original_host == redirect_host
    except Exception:
        return False


@dataclass
class FetchedContent:
    content: str
    bytes_: int
    code: int
    code_text: str
    content_type: str
    persisted_path: Optional[str] = None
    persisted_size: Optional[int] = None


@dataclass
class RedirectInfo:
    type: str = "redirect"
    original_url: str = ""
    redirect_url: str = ""
    status_code: int = 0
